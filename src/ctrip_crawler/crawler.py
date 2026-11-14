"""
携程（Ctrip）景点与用户点评智能采集引擎
特性：
1. 默认开启 CDP（支持自动接管与自动拉起本地 Chrome/Edge 调试端口）
2. 智能检索：直接输入目的地/景点名即可自动解析并导航至点评主页
3. 批量采集：支持从 Excel (.xlsx/.xls) 或 CSV 文件中导入景点名单批量爬取
4. 多格式输出：无缝支持 CSV, Excel, SQLite, JSON 以及多格式同步导出
"""

import asyncio
import hashlib
import logging
import os
import random
import re
import subprocess
from urllib.parse import quote

import pandas as pd
from playwright.async_api import Page, async_playwright

from . import selectors as S
from .browser_manager import ensure_cdp_browser
from .exporter import DataExporter

logger = logging.getLogger(__name__)

# 表格中可能作为「景点名单」的列名（小写比较）
SPOT_COLUMN_CANDIDATES = (
    "景点",
    "景区",
    "景点名称",
    "景区名称",
    "目的地",
    "名称",
    "关键词",
    "spot",
    "scenic_spot",
    "name",
    "keyword",
)

# 导航重试次数与重试间隔
NAV_RETRY = 3
NAV_RETRY_DELAY = 3.0
# 导航超时
NAV_TIMEOUT = 60000


class CtripSightCrawler:
    def __init__(
        self,
        use_cdp: bool = True,
        cdp_port: int = 9222,
        headless: bool = True,
    ):
        """
        :param use_cdp: 是否开启 CDP 模式（默认 True，防反爬能力最强）
        :param cdp_port: CDP 调试端口（默认 9222）
        :param headless: 是否使用无头模式
        """
        self.use_cdp = use_cdp
        self.cdp_port = cdp_port
        self.headless = headless
        self.records: list[dict] = []
        self.seen_keys: set = set()
        self._browser_proc: subprocess.Popen | None = None

    @staticmethod
    def normalize_spot_list(spots: str | list[str]) -> list[str]:
        """把入参统一成去重后的景点列表（去掉空白项，保留首次出现的顺序）。"""
        if isinstance(spots, str):
            raw = [spots]
        else:
            raw = list(spots)
        return list(dict.fromkeys(s for s in raw if s and s.strip()))

    # ------------------------------------------------------------------
    # 输入：批量名单解析
    # ------------------------------------------------------------------
    @staticmethod
    def load_spots_from_file(file_path: str) -> list[str]:
        """从 Excel 或 CSV 文件中自动提取景点/目的地名称列表"""
        if not os.path.exists(file_path):
            raise FileNotFoundError(f"找不到输入的批量文件: {file_path}")

        ext = os.path.splitext(file_path)[1].lower()
        if ext in (".xlsx", ".xls"):
            # 显式指定引擎：.xls 需要 calamine，.xlsx 用 openpyxl（避免 pandas 未来默认引擎变更告警）
            engine = "calamine" if ext == ".xls" else "openpyxl"
            df = pd.read_excel(file_path, engine=engine)
        elif ext == ".csv":
            try:
                df = pd.read_csv(file_path, encoding="utf-8-sig")
            except Exception:
                df = pd.read_csv(file_path, encoding="gbk")
        else:
            raise ValueError(f"不支持的文件格式: {ext}，仅支持 .xlsx, .xls, .csv")

        if df is None or len(df.columns) == 0:
            raise ValueError(f"表格 {file_path} 中没有任何列，无法读取景点名单。")

        # 优先匹配常见的列名
        target_col = None
        for col in df.columns:
            if str(col).strip().lower() in SPOT_COLUMN_CANDIDATES:
                target_col = col
                break

        # 如果没有匹配到列名，默认使用第一列
        if target_col is None:
            target_col = df.columns[0]
            logger.info("未在表格中发现标准列名，默认使用首列 [%s] 作为景点名单。", target_col)
        else:
            logger.info("成功识别景点所在列: [%s]", target_col)

        spots = [str(x).strip() for x in df[target_col].dropna().tolist() if str(x).strip()]

        # 名单去重，避免同一景点被重复爬取
        deduped = list(dict.fromkeys(spots))
        if len(deduped) != len(spots):
            logger.info("名单去重：%d 条 -> %d 条", len(spots), len(deduped))
        logger.info("从文件读取到 %d 个待采集景点: %s", len(deduped), deduped[:5])
        return deduped

    # ------------------------------------------------------------------
    # 景点 URL 解析
    # ------------------------------------------------------------------
    async def _resolve_spot_url(self, page: Page, spot_input: str) -> tuple[str, str]:
        """
        智能解析输入：
        - 若已是 URL，直接返回；
        - 若是名称/目的地，通过携程全局搜索 API 自动检索匹配最佳景点点评页面
        """
        # 0. 优先检测：若当前标签页正好就是携程景点/点评页，直接复用当前页面！
        current_url = page.url or ""
        if S.SIGHT_URL_MARKER in current_url:
            page_title = await page.title()
            if not spot_input or spot_input in page_title or spot_input in current_url:
                logger.info("发现当前浏览器正停留在目标景点页面: 【%s】", page_title)
                logger.info("无需重复搜索，直接在当前标签页开始采集！")
                name = page_title.split(S.TITLE_SUFFIX)[0].strip()
                return current_url, name or spot_input or "当前景点"

        spot_input = spot_input.strip()
        if spot_input.startswith("http://") or spot_input.startswith("https://"):
            return spot_input, "指定景点"

        logger.info("正在智能检索目的地/景点: [%s] ...", spot_input)
        search_url = f"https://you.ctrip.com/globalsearch/?keyword={quote(spot_input)}"

        captured_sights: list[dict] = []

        async def _on_search_response(resp):
            if S.SEARCH_API_KEYWORD not in resp.url:
                return
            try:
                data = await resp.json()
            except Exception:
                return
            for home_item in data.get("homeItems", []):
                for card in home_item.get("items", []):
                    u = card.get("url") or ""
                    if S.SIGHT_URL_MARKER in u:
                        captured_sights.append(
                            {
                                "name": card.get("word") or card.get("name") or spot_input,
                                "url": u,
                                "score": card.get("commentScoreText") or "",
                                "count": card.get("commentCountText") or "",
                            }
                        )

        page.on("response", _on_search_response)
        try:
            await page.goto(search_url, wait_until="domcontentloaded", timeout=45000)
            # 给前端渲染与接口返回留出时间
            for _ in range(10):
                await asyncio.sleep(0.5)
                if captured_sights:
                    break
        except Exception as exc:
            logger.warning("访问搜索页异常: %s", exc)
        finally:
            # 必须移除监听器，否则批量任务中监听器会不断累积
            try:
                page.remove_listener("response", _on_search_response)
            except Exception:
                pass

        # 优先使用 API 捕获的 sight 卡片，按与查询词的相关度排序
        if captured_sights:
            best = _pick_best_sight(captured_sights, spot_input)
            logger.info(
                "智能检索成功！匹配到: 【%s】(%s) -> %s", best["name"], best["score"], best["url"]
            )
            return best["url"], best["name"]

        # 兜底：DOM 中直接查找 a[href*="/sight/"]
        links = await page.locator(S.SEARCH_SIGHT_LINK).all()
        dom_sights = []
        for link in links:
            href = await link.get_attribute("href")
            text = (await link.inner_text()).strip().replace("\n", " ")
            if href and text and not href.startswith("javascript"):
                dom_sights.append({"name": text, "url": href})
        if dom_sights:
            best = _pick_best_sight(dom_sights, spot_input)
            logger.info("从页面卡片检索到: 【%s】 -> %s", best["name"], best["url"])
            return best["url"], best["name"]

        raise RuntimeError(
            f"未能自动检索到景点 [{spot_input}] 对应的携程点评主页，请检查名称是否准确。"
        )

    # ------------------------------------------------------------------
    # 点评抽取
    # ------------------------------------------------------------------
    async def _extract_page_comments(self, page: Page, spot_name: str) -> int:
        """从当前页点评卡片中提取数据"""
        items = await page.locator(S.COMMENT_ITEM).all()
        new_count = 0

        for item in items:
            # 1. 评论正文
            detail_loc = item.locator(S.COMMENT_DETAIL)
            if not await detail_loc.count():
                continue
            content = (await detail_loc.inner_text()).strip().replace("\r", " ").replace("\n", " ")
            if not content:
                continue

            # 2. 用户名
            user_loc = item.locator(S.USER_NAME)
            user_name = (
                (await user_loc.inner_text()).strip() if await user_loc.count() else "匿名用户"
            )

            # 3. 时间与 IP 属地
            time_loc = item.locator(S.COMMENT_TIME)
            time_raw = (await time_loc.inner_text()).strip() if await time_loc.count() else ""
            publish_time, ip_location = "", ""
            if time_raw:
                date_match = re.search(r"\d{4}-\d{2}-\d{2}", time_raw)
                if date_match:
                    publish_time = date_match.group(0)
                ip_match = re.search(r"IP属地[：:]\s*([^\s]+)", time_raw)
                if ip_match:
                    ip_location = ip_match.group(1)

            # 4. 评分
            score_loc = item.locator(S.SCORE)
            score = (await score_loc.inner_text()).strip() if await score_loc.count() else ""

            # 去重：对全文做哈希，避免前 25 字相同的两条点评被误判为重复
            digest = hashlib.md5(content.encode("utf-8")).hexdigest()
            unique_key = f"{spot_name}_{user_name}_{digest}"
            if unique_key in self.seen_keys:
                continue

            self.seen_keys.add(unique_key)
            new_count += 1

            self.records.append(
                {
                    "景区名称": spot_name,
                    "平台": "携程",
                    "内容类型": "用户点评",
                    "时间": publish_time or time_raw,
                    "文本内容": content,
                    "字数": len(content),
                    "评分": score,
                    "IP属地": ip_location,
                    "用户名": user_name,
                }
            )
            logger.info(
                "    [+] 采集点评: [%s] %s (%s): %s...",
                publish_time or "未知时间",
                user_name,
                score,
                content[:35],
            )

        return new_count

    async def _goto_with_retry(self, page: Page, url: str) -> bool:
        """带重试的页面导航，失败时返回 False。"""
        for attempt in range(1, NAV_RETRY + 1):
            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=NAV_TIMEOUT)
                return True
            except Exception as exc:
                if attempt < NAV_RETRY:
                    logger.warning(
                        "打开页面失败（第 %d/%d 次）: %s，%.1f 秒后重试",
                        attempt,
                        NAV_RETRY,
                        exc,
                        NAV_RETRY_DELAY,
                    )
                    await asyncio.sleep(NAV_RETRY_DELAY)
                else:
                    logger.error("打开页面最终失败: %s", exc)
        return False

    # ------------------------------------------------------------------
    # 单景点采集
    # ------------------------------------------------------------------
    async def crawl_single(
        self,
        page: Page,
        spot_input: str,
        max_pages: int = 3,
    ) -> int:
        """采集单个景点的评论数据"""
        try:
            target_url, spot_name = await self._resolve_spot_url(page, spot_input)
        except Exception as exc:
            logger.warning("解析景点 [%s] 失败: %s", spot_input, exc)
            return 0

        logger.info("导航至景点点评主页: %s", target_url)
        if not await self._goto_with_retry(page, target_url):
            logger.warning("[!] 无法打开点评主页，跳过该景点。")
            return 0

        # 定位评论容器
        comment_loc = page.locator(S.COMMENT_LIST)
        try:
            await comment_loc.wait_for(state="attached", timeout=15000)
            await comment_loc.scroll_into_view_if_needed()
            await asyncio.sleep(1.5)
        except Exception:
            logger.info("未能定位到点评列表 (%s)，该景点可能暂无用户评论。", S.COMMENT_LIST)
            return 0

        spot_start_count = len(self.records)

        for current_page in range(1, max_pages + 1):
            logger.info("  [>>> %s - 正在采集第 %d/%d 页 <<<]", spot_name, current_page, max_pages)
            new_added = await self._extract_page_comments(page, spot_name)
            logger.info(
                "    [-] 本页新增: %d 条 (该景点累计: %d 条)",
                new_added,
                len(self.records) - spot_start_count,
            )

            if current_page >= max_pages:
                break

            # 翻页
            next_btn = page.locator(S.NEXT_PAGE_BUTTON)
            if not await next_btn.count():
                logger.info("    未找到下一页控件，已到末页。")
                break

            btn_class = await next_btn.get_attribute("class") or ""
            if S.DISABLED_CLASS in btn_class:
                logger.info("    下一页按钮已禁用 (已无更多点评)。")
                break

            first_text = ""
            first_detail = page.locator(f"{S.COMMENT_ITEM} {S.COMMENT_DETAIL}").first
            if await first_detail.count():
                first_text = (await first_detail.inner_text()).strip()

            await next_btn.scroll_into_view_if_needed()
            await next_btn.click()

            # 等待刷新
            refreshed = False
            for _ in range(20):
                await asyncio.sleep(0.5)
                new_detail = page.locator(f"{S.COMMENT_ITEM} {S.COMMENT_DETAIL}").first
                if await new_detail.count():
                    cur_text = (await new_detail.inner_text()).strip()
                    if cur_text and cur_text != first_text:
                        refreshed = True
                        break

            if not refreshed:
                await asyncio.sleep(2)

            await asyncio.sleep(random.uniform(1.5, 3.0))

        return len(self.records) - spot_start_count

    # ------------------------------------------------------------------
    # 主流程
    # ------------------------------------------------------------------
    async def run(
        self,
        spots: str | list[str],
        max_pages: int = 3,
        output_file: str = "ctrip_comments.csv",
        format_type: str = "auto",
    ):
        """
        启动采集主流程
        :param spots: 单个景点名称/URL，或者景点名称列表
        :param max_pages: 每个景点最大采集翻页数
        :param output_file: 导出文件名或路径
        :param format_type: auto | csv | excel | sqlite | json | all
        """
        # 名单去重，避免同一景点被重复请求
        spot_list = self.normalize_spot_list(spots)

        if not spot_list:
            logger.warning("采集名单为空，任务终止。")
            return

        async with async_playwright() as p:
            browser = None
            page: Page | None = None

            try:
                if self.use_cdp:
                    endpoint, proc = ensure_cdp_browser(
                        port=self.cdp_port,
                        headless=self.headless,
                    )
                    self._browser_proc = proc
                    try:
                        browser = await p.chromium.connect_over_cdp(endpoint)
                        context = (
                            browser.contexts[0] if browser.contexts else await browser.new_context()
                        )
                        # 优先复用当前已经打开的标签页，绝不重复新开！
                        if context.pages:
                            page = context.pages[-1]
                            logger.info("成功接管当前打开的浏览器标签页: %s", page.url or "空白页")
                        else:
                            page = await context.new_page()
                    except Exception as exc:
                        logger.error("连接 CDP 浏览器失败: %s", exc)
                        self._cleanup_browser_process()
                        return
                else:
                    logger.info("运行模式: 原生自动化驱动 (headless=%s)", self.headless)
                    for channel in ["msedge", "chrome", None]:
                        try:
                            browser = await p.chromium.launch(
                                channel=channel, headless=self.headless
                            )
                            logger.info("成功启动浏览器内核: %s", channel or "Playwright Chromium")
                            break
                        except Exception:
                            continue
                    if not browser:
                        raise RuntimeError("无法启动浏览器，请安装 Edge/Chrome。")
                    page = await browser.new_page()

                # 遍历采集
                total_spots = len(spot_list)
                for idx, spot_item in enumerate(spot_list, start=1):
                    logger.info("#" * 65)
                    logger.info("[任务进度 %d/%d] 准备处理景点: %s", idx, total_spots, spot_item)
                    logger.info("#" * 65)

                    await self.crawl_single(page, spot_item, max_pages=max_pages)

                    # 批量任务间友好随机等待
                    if idx < total_spots:
                        interval = random.uniform(2.5, 4.5)
                        logger.info("景点间间隔休眠 %.1f 秒，防止触发平台风控...", interval)
                        await asyncio.sleep(interval)
            finally:
                if not self.use_cdp:
                    if page:
                        await page.close()
                    if browser:
                        await browser.close()
                else:
                    logger.info("CDP 模式：已保留当前浏览器与页面窗口，未执行关闭。")

        # 导出结果
        DataExporter.export(
            records=self.records,
            output_path=output_file,
            format_type=format_type,
        )

    def _cleanup_browser_process(self) -> None:
        """异常路径下结束我们自己拉起的浏览器进程，避免留下孤儿进程。"""
        if self._browser_proc is None:
            return
        try:
            self._browser_proc.terminate()
            logger.info("已结束本次自动拉起的浏览器进程。")
        except Exception:
            pass
        finally:
            self._browser_proc = None


def _pick_best_sight(candidates: list[dict], query: str) -> dict:
    """
    从搜索结果中挑出与查询词最相关的一条。

    搜索接口返回顺序并不保证与关键词相关，因此按以下规则打分：
    1. 名称完全相等（忽略大小写与空格）-> 最高分
    2. 名称包含查询词 / 查询词包含名称 -> 较高分
    3. 字符重合度 -> 兜底分
    """
    if not candidates:
        raise RuntimeError("没有可用的候选景点。")

    q = _normalize(query)
    best = None
    best_score = -1.0

    for cand in candidates:
        name = _normalize(cand.get("name") or "")
        score = 0.0
        if name and name == q:
            score = 100.0
        elif q and name and (q in name or name in q):
            score = 60.0 + min(len(name), len(q)) / max(len(name), len(q)) * 10
        else:
            common = len(set(q) & set(name)) if q and name else 0
            score = common * 5.0

        # 同分时保留靠前的结果
        if score > best_score:
            best_score = score
            best = cand

    return best or candidates[0]


def _normalize(text: str) -> str:
    """归一化文本用于比较：去空白、转小写。"""
    return re.sub(r"\s+", "", (text or "")).lower()
