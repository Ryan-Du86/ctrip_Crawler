"""
携程（Ctrip）景点与用户点评智能采集引擎
特性：
1. 默认开启 CDP（支持自动接管与自动拉起本地 Chrome/Edge 调试端口）
2. 智能检索：直接输入目的地/景点名即可自动解析并导航至点评主页
3. 批量采集：支持从 Excel (.xlsx/.xls) 或 CSV 文件中导入景点名单批量爬取
4. 多格式输出：无缝支持 CSV, Excel, SQLite, JSON 以及多格式同步导出
5. 登录态检测：复用浏览器已有会话，可选强制要求登录
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

from . import aliases
from . import selectors as S
from .browser_manager import ensure_cdp_browser
from .exporter import DataExporter
from .login import LoginState, ensure_login, summarize

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

# 别名映射表放在 data/alias_sight_map.json，由 aliases 模块负责加载。
# 需要增补映射时只改数据文件即可，无需改动代码。
# 命中的别名在抓不到点评时会给出对应的提示文案（见 aliases.get_hint）。

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
        require_login: bool = False,
        login_wait: int = 0,
        popup_qr: bool = True,
    ):
        """
        :param use_cdp: 是否开启 CDP 模式（默认 True，防反爬能力最强）
        :param cdp_port: CDP 调试端口（默认 9222）
        :param headless: 是否使用无头模式
        :param require_login: 是否必须登录才能继续（未登录时直接报错退出）
        :param login_wait: 未登录时等待用户完成登录的秒数（0 表示不等待）
        :param popup_qr: 是否自动将登录二维码截图并通过系统默认看图软件弹出（方便无头扫码）
        """
        self.use_cdp = use_cdp
        self.cdp_port = cdp_port
        self.headless = headless
        self.require_login = require_login
        self.login_wait = login_wait
        self.popup_qr = popup_qr
        self.records: list[dict] = []
        self.seen_keys: set[str] = set()
        self.login_state: LoginState | None = None
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
            raise ValueError(f"Spreadsheet {file_path} has no columns; cannot read the spot list.")

        # 优先匹配常见的列名
        target_col = None
        for col in df.columns:
            if str(col).strip().lower() in SPOT_COLUMN_CANDIDATES:
                target_col = col
                break

        # 如果没有匹配到列名，默认使用第一列
        if target_col is None:
            target_col = df.columns[0]
            logger.info("No standard column name found; using the first column [%s].", target_col)
        else:
            logger.info("Detected the spot column: [%s]", target_col)

        spots = [str(x).strip() for x in df[target_col].dropna().tolist() if str(x).strip()]

        # 名单去重，避免同一景点被重复爬取
        deduped = list(dict.fromkeys(spots))
        if len(deduped) != len(spots):
            logger.info("Spot list de-duplicated: %d -> %d", len(spots), len(deduped))
        logger.info("Loaded %d attraction(s) from file: %s", len(deduped), deduped[:5])
        return deduped

    # ------------------------------------------------------------------
    # 景点 URL 解析
    # ------------------------------------------------------------------
    @staticmethod
    def _is_sight_url(url: str) -> bool:
        """
        判断一个 URL 是否为携程景点详情页。

        同时校验域名与路径特征，避免把携程的其它页面（商城、攻略等）
        误当成景点点评页而抓不到数据。

        :param url: 待校验的 URL
        :return: 是携程景点页时返回 True
        """
        if not url:
            return False
        lowered = url.lower()
        if S.SIGHT_URL_HOST not in lowered:
            return False
        return S.SIGHT_URL_MARKER in lowered

    async def _resolve_spot_url(
        self,
        page: Page,
        spot_input: str,
        allow_alias: bool = True,
    ) -> tuple[str, str, str]:
        """
        智能解析输入：
        - 若已是 URL，直接返回；
        - 若是名称/目的地，先查别名映射表，再通过携程全局搜索 API 检索最佳景点点评页面

        :param page: 用于检索与校验的页面
        :param spot_input: 用户输入的景点名称或 URL
        :param allow_alias: 是否允许使用别名映射表
        :return: (目标 URL, 景点名, 来源标记)；来源标记取值 alias / url / search / current
        """
        # 0. 优先检测：若当前标签页正好就是携程景点/点评页，直接复用当前页面！
        current_url = page.url or ""
        if self._is_sight_url(current_url):
            page_title = await page.title()
            if not spot_input or spot_input in page_title or spot_input in current_url:
                logger.info(
                    "Current tab is already on the target attraction page: [%s]", page_title
                )
                logger.info("Skipping search; crawling the current tab directly.")
                name = page_title.split(S.TITLE_SUFFIX)[0].split("-")[0].split("_")[0].strip()
                return current_url, name or spot_input or "Current attraction", "current"

        spot_input = spot_input.strip()
        # 1. 优先查别名映射表
        alias_hit = aliases.find_alias(spot_input) if allow_alias else None
        if alias_hit:
            alias_key, mapped_url = alias_hit
            logger.info(
                "智能知识库匹配: [%s] -> 自动映射至官方主干收录页面: %s", alias_key, mapped_url
            )
            return mapped_url, alias_key, "alias"

        if spot_input.startswith("http://") or spot_input.startswith("https://"):
            return spot_input, "Given URL", "url"

        logger.info("Searching for attraction: [%s] ...", spot_input)
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
                    if self._is_sight_url(u):
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
            logger.warning("Search page request failed: %s", exc)
        finally:
            # 必须移除监听器，否则批量任务中监听器会不断累积
            try:
                page.remove_listener("response", _on_search_response)
            except Exception:
                pass

        # 优先使用 API 捕获的 sight 卡片，按与查询词的相关度排序
        if captured_sights:
            best = _pick_best_sight(captured_sights, spot_input)
            logger.info("Search matched: [%s] (%s) -> %s", best["name"], best["score"], best["url"])
            return best["url"], best["name"], "search"

        # 兜底：DOM 中直接查找 a[href*="/sight/"]
        links = await page.locator(S.SEARCH_SIGHT_LINK).all()
        dom_sights = []
        for link in links:
            href = await link.get_attribute("href")
            text = (await link.inner_text()).strip().replace("\n", " ")
            if href and text and not href.startswith("javascript") and self._is_sight_url(href):
                dom_sights.append({"name": text, "url": href})
        if dom_sights:
            best = _pick_best_sight(dom_sights, spot_input)
            logger.info("Resolved from page cards: [%s] -> %s", best["name"], best["url"])
            return best["url"], best["name"], "search"

        raise RuntimeError(
            f"Could not find a Ctrip review page for [{spot_input}]. "
            "Check that the attraction name is correct."
        )

    # ------------------------------------------------------------------
    # 点评抽取
    # ------------------------------------------------------------------
    async def _extract_page_comments(
        self,
        page: Page,
        spot_name: str,
        official_meta: dict[str, str] | None = None,
    ) -> int:
        """从当前页点评卡片中提取数据（单次 evaluate 批量抽取，减少 CDP 往返）"""
        rows = await self._read_page_rows(page)
        new_count = 0

        for row in rows:
            content = (
                (row.get("content") or "").replace("\r", " ").replace("\n", " ").strip()
            )
            if not content:
                continue

            user_name = row.get("username") or "匿名用户"
            time_raw = row.get("time") or ""
            publish_time, ip_location = "", ""
            if time_raw:
                date_match = re.search(r"\d{4}-\d{2}-\d{2}", time_raw)
                if date_match:
                    publish_time = date_match.group(0)
                ip_match = re.search(r"IP属地[：:]\s*([^\s]+)", time_raw)
                if ip_match:
                    ip_location = ip_match.group(1)
            score = row.get("score") or ""

            # 去重：对全文做哈希，避免前 25 字相同的两条点评被误判为重复
            digest = hashlib.md5(content.encode("utf-8")).hexdigest()
            unique_key = f"{spot_name}_{user_name}_{digest}"
            if unique_key in self.seen_keys:
                continue

            self.seen_keys.add(unique_key)
            new_count += 1

            record: dict[str, str] = {
                "景区名称": spot_name,
                "官方规范名称": (official_meta.get("officialName") if official_meta else "") or spot_name,
                "官方分类与榜单": (official_meta.get("rankTag") if official_meta else "") or "",
                "景区综合评分": (official_meta.get("score") if official_meta else "") or "",
                "用户名": user_name,
                "评分": score,
                "时间": publish_time or time_raw,
                "IP属地": ip_location,
                "文本内容": content,
            }
            self.records.append(record)
            logger.info(
                "    [+] Captured: [%s] %s (%s): %s...",
                publish_time or "unknown date",
                user_name,
                score,
                content[:35],
            )

        return new_count

    @staticmethod
    async def _read_page_rows(page: Page) -> list[dict]:
        """
        读取当前页全部点评卡片字段，返回 [{content, username, time, score}, ...]。

        首选一次 evaluate 批量抽取（整页一次往返）；
        脚本失败时退回逐条 locator 模式，保证兼容性。
        """
        try:
            return await page.evaluate(_BATCH_EXTRACT_JS, _dom_selector_args())
        except Exception as exc:
            logger.debug("Batch DOM extraction failed (%s); falling back to locator mode.", exc)
            return await _extract_rows_via_locators(page)

    async def _goto_with_retry(self, page: Page, url: str) -> bool:
        """带重试的页面导航，失败时返回 False。"""
        for attempt in range(1, NAV_RETRY + 1):
            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=NAV_TIMEOUT)
                return True
            except Exception as exc:
                if attempt < NAV_RETRY:
                    logger.warning(
                        "Navigation failed (attempt %d/%d): %s; retrying in %.1fs",
                        attempt,
                        NAV_RETRY,
                        exc,
                        NAV_RETRY_DELAY,
                    )
                    await asyncio.sleep(NAV_RETRY_DELAY)
                else:
                    logger.error("Navigation failed after all retries: %s", exc)
        return False

    # ------------------------------------------------------------------
    # 单景点采集
    # ------------------------------------------------------------------
    async def crawl_single(
        self,
        page: Page,
        spot_input: str,
        max_pages: int = 3,
        _allow_alias: bool = True,
    ) -> int:
        """
        采集单个景点的评论数据。

        :param page: 使用的页面
        :param spot_input: 景点名称或 URL
        :param max_pages: 最大翻页数
        :param _allow_alias: 是否允许使用别名映射。映射页面失效时会以 False 重试，
                             避免在同一张失效的映射上反复打转。
        """
        try:
            target_url, spot_name, source = await self._resolve_spot_url(
                page, spot_input, allow_alias=_allow_alias
            )
        except Exception as exc:
            logger.warning("Could not resolve attraction [%s]: %s", spot_input, exc)
            return 0

        logger.info("Navigating to the attraction review page: %s", target_url)
        if not await self._goto_with_retry(page, target_url):
            logger.warning("Could not open the review page; skipping this attraction.")
            return 0

        # 若是通过直接传入 URL 采集，从页面标题智能解析真实景点名称
        if not spot_name or spot_name == "Given URL" or spot_name.startswith("http"):
            try:
                page_title = await page.title()
                if S.TITLE_SUFFIX in page_title:
                    extracted = page_title.split(S.TITLE_SUFFIX)[0].strip()
                else:
                    extracted = page_title.split("-")[0].split("_")[0].strip()
                if extracted:
                    spot_name = extracted
                    logger.info("Resolved attraction name from page title: [%s]", spot_name)
            except Exception:
                pass
            if not spot_name or spot_name == "Given URL":
                spot_name = spot_input

        # 定位评论容器
        comment_loc = page.locator(S.COMMENT_LIST)
        try:
            await comment_loc.wait_for(state="attached", timeout=15000)
            await comment_loc.scroll_into_view_if_needed()
            await asyncio.sleep(1.5)
        except Exception:
            logger.info(
                "Review list (%s) not found; this attraction may have no reviews yet.",
                S.COMMENT_LIST,
            )
            # 映射表里的 URL 可能因携程改版失效，此时回退到搜索流程重新解析，
            # 而不是直接放弃这个景点。
            if source == "alias":
                logger.warning(
                    "别名映射出的页面没有点评内容（可能已失效），回退到搜索流程重新解析..."
                )
                return await self.crawl_single(page, spot_input, max_pages, _allow_alias=False)
            hint = aliases.get_hint(spot_name)
            if hint:
                logger.warning("提示：%s", hint)
            return 0

        # 抽取页面官方分类、榜单荣誉与综合评分等元数据
        official_meta: dict[str, str] = {}
        try:
            official_meta = await page.evaluate(_OFFICIAL_META_JS, _official_meta_args())
        except Exception:
            official_meta = {}

        if official_meta.get("officialName"):
            logger.info(
                "Official metadata: [%s] | Tag: [%s] | Score: [%s]",
                official_meta["officialName"],
                official_meta.get("rankTag") or "None",
                official_meta.get("score") or "None",
            )

        spot_start_count = len(self.records)

        for current_page in range(1, max_pages + 1):
            logger.info("  [>>> %s - page %d/%d <<<]", spot_name, current_page, max_pages)
            new_added = await self._extract_page_comments(page, spot_name, official_meta)
            logger.info(
                "    [-] New on this page: %d (total for this attraction: %d)",
                new_added,
                len(self.records) - spot_start_count,
            )

            if current_page >= max_pages:
                break

            # 翻页
            next_btn = page.locator(S.NEXT_PAGE_BUTTON)
            if not await next_btn.count():
                logger.info("    No next-page control found; already at the last page.")
                break

            btn_class = await next_btn.get_attribute("class") or ""
            if S.DISABLED_CLASS in btn_class:
                logger.info("    Next-page button is disabled (no more reviews).")
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
            logger.warning("The attraction list is empty; nothing to crawl.")
            return

        try:
            await self._run_crawl(spot_list, max_pages)
        finally:
            # 无论正常结束还是中途报错，已经采到的数据都要落盘，
            # 否则 CDP 连接失败之类的异常会静默丢掉整轮采集结果。
            self._export(output_file, format_type)

    async def _run_crawl(self, spot_list: list[str], max_pages: int) -> None:
        """
        浏览器交互部分：连接浏览器、检测登录态、逐个景点采集。

        异常直接向上抛出，由 :meth:`run` 统一在 finally 中导出数据。
        """
        async with async_playwright() as p:
            browser = None
            page: Page | None = None
            connected = False

            try:
                if self.use_cdp:
                    # 拉起浏览器的同步轮询最长可达 10s，放到线程池避免阻塞事件循环
                    endpoint, proc = await asyncio.to_thread(
                        ensure_cdp_browser,
                        self.cdp_port,
                        None,
                        self.headless,
                    )
                    self._browser_proc = proc
                    self._print_cdp_banner("CONNECTING", endpoint)
                    try:
                        browser = await p.chromium.connect_over_cdp(endpoint)
                    except Exception as exc:
                        self._print_cdp_banner("FAILED", endpoint, str(exc))
                        logger.error("Failed to connect to the CDP browser: %s", exc)
                        self._cleanup_browser_process()
                        raise

                    context = (
                        browser.contexts[0] if browser.contexts else await browser.new_context()
                    )
                    # 优先复用已经打开的景点标签页，避免占用用户正在用的其它页面
                    page = await self._pick_target_page(context)
                    self._print_cdp_banner("CONNECTED", endpoint, detail=page.url or "blank page")
                    connected = True
                else:
                    logger.info("Run mode: native automation driver (headless=%s)", self.headless)
                    for channel in ["msedge", "chrome", None]:
                        try:
                            browser = await p.chromium.launch(
                                channel=channel, headless=self.headless
                            )
                            logger.info(
                                "Browser engine started: %s", channel or "Playwright Chromium"
                            )
                            break
                        except Exception:
                            continue
                    if not browser:
                        raise RuntimeError(
                            "Could not start a browser; please install Edge or Chrome."
                        )
                    page = await browser.new_page()

                # 登录态检测：复用浏览器已有会话，避免匿名采集受限
                self.login_state = await self._check_login(page)

                # 遍历采集
                total_spots = len(spot_list)
                for idx, spot_item in enumerate(spot_list, start=1):
                    logger.info("#" * 65)
                    logger.info(
                        "[Task %d/%d] Processing attraction: %s", idx, total_spots, spot_item
                    )
                    logger.info("#" * 65)

                    await self.crawl_single(page, spot_item, max_pages=max_pages)

                    # 批量任务间友好随机等待
                    if idx < total_spots:
                        interval = random.uniform(2.5, 4.5)
                        logger.info(
                            "Sleeping %.1fs between attractions to avoid rate limiting...", interval
                        )
                        await asyncio.sleep(interval)
            finally:
                if not self.use_cdp:
                    if page:
                        await page.close()
                    if browser:
                        await browser.close()
                else:
                    if connected:
                        self._print_cdp_banner("DISCONNECTED", detail="browser left open")
                    self._cleanup_browser_process()

    def _export(self, output_file: str, format_type: str) -> None:
        """
        导出采集结果。导出失败只记录日志，不掩盖采集阶段的真实异常。
        """
        try:
            DataExporter.export(
                records=self.records,
                output_path=output_file,
                format_type=format_type,
            )
        except Exception as exc:
            logger.error("Failed to export the collected reviews: %s", exc)

    @staticmethod
    async def _pick_target_page(context) -> Page:
        """
        在已打开的标签页中挑一个适合采集的页面。

        优先级：
        1. 已经在携程景点页上的标签页 —— 直接复用，不打扰用户；
        2. 其它已打开的标签页 —— 复用第一个，避免无谓地多开窗口；
        3. 都没有时新开一个标签页。

        :param context: 浏览器上下文
        :return: 选中的页面
        """
        pages = list(context.pages)

        for candidate in pages:
            url = (candidate.url or "").lower()
            if S.SIGHT_URL_HOST in url and S.SIGHT_URL_MARKER in url:
                logger.info("Reusing the tab already on a Ctrip attraction page.")
                return candidate

        if pages:
            logger.info("Reusing the first open tab.")
            return pages[0]

        logger.info("No open tab found; opening a new one.")
        return await context.new_page()

    def _cleanup_browser_process(self) -> None:
        """异常路径下结束我们自己拉起的浏览器进程，避免留下孤儿进程。"""
        if self._browser_proc is None:
            return
        try:
            self._browser_proc.terminate()
            logger.info("Terminated the browser process started by this run.")
        except Exception:
            pass
        finally:
            self._browser_proc = None

    @staticmethod
    def _print_cdp_banner(status: str, endpoint: str = "", detail: str = "") -> None:
        """
        在终端打印 CDP 连接状态横幅。

        只输出 ASCII 字符，避免 Windows 旧版控制台编码问题。
        """
        marker = {
            "CONNECTING": "[...]",
            "CONNECTED": "[ OK ]",
            "FAILED": "[FAIL]",
            "DISCONNECTED": "[ ... ]",
        }.get(status, "[ ... ]")

        parts = [f"CDP {status:<12}"]
        if endpoint:
            parts.append(f"endpoint: {endpoint}")
        if detail:
            parts.append(f"tab: {detail}")
        logger.info("%s %s", marker, " | ".join(parts))

    async def _check_login(self, page: Page) -> LoginState:
        """
        检查浏览器登录态，必要时等待用户登录。

        复用浏览器里已有的会话，所以只要你在同一个浏览器窗口登录过携程，
        这里就能直接识别到，不会再以匿名身份采集。
        """
        state = await ensure_login(
            page,
            require_login=self.require_login,
            wait_timeout=self.login_wait,
            popup_qr=self.popup_qr,
        )
        logger.info("Session: %s", summarize(state))
        return state


async def _extract_text(scope, selectors: tuple[str, ...]) -> str:
    """
    Return the text of the first matching selector inside ``scope``.

    Each selector is tried in order, so a caller can list the current markup
    first and legacy spellings afterwards.

    :param scope: a Playwright locator (a comment card)
    :param selectors: candidate selectors, in priority order
    :return: the trimmed text, or an empty string when nothing matched
    """
    for selector in selectors:
        try:
            loc = scope.locator(selector).first
            if await loc.count():
                text = (await loc.inner_text()).strip()
                if text:
                    return text
        except Exception:
            continue
    return ""


async def _extract_rows_via_locators(page: Page) -> list[dict]:
    """逐条 locator 抽取整页点评字段（批量 evaluate 失败时的兜底方案）。"""
    items = await page.locator(S.COMMENT_ITEM).all()
    rows: list[dict] = []
    for item in items:
        content = await _extract_text(item, (S.COMMENT_DETAIL,))
        if not content:
            continue
        rows.append(
            {
                "content": content,
                "username": await _extract_text(item, (S.USER_NAME,)),
                "time": await _extract_text(item, (S.COMMENT_TIME,)),
                "score": await _extract_text(item, (S.SCORE, *S.SCORE_FALLBACK)),
            }
        )
    return rows


# 景点页官方元数据（规范名 / 榜单标签 / 综合评分）抽取脚本。
# 候选选择器全部来自 selectors.py，按优先级依次尝试，取第一个非空文本。
_OFFICIAL_META_JS = """
(args) => {
  const getTxt = (selectors) => {
    for (const sel of selectors) {
      const el = document.querySelector(sel);
      if (el) {
        const t = (el.innerText || el.textContent || '').trim();
        if (t) return t;
      }
    }
    return '';
  };
  return {
    officialName: getTxt(args.officialName),
    rankTag: getTxt(args.rankTag),
    score: getTxt(args.score),
  };
}
"""


def _official_meta_args() -> dict:
    """构造官方元数据抽取脚本的选择器入参（与 selectors.py 保持同源）。"""
    return {
        "officialName": list(S.OFFICIAL_NAME),
        "rankTag": list(S.OFFICIAL_RANK_TAG),
        "score": list(S.OFFICIAL_SCORE),
    }


# 整页点评字段的一次性批量抽取脚本：每个字段同样按候选选择器顺序兜底。
_BATCH_EXTRACT_JS = """
(args) => {
  const pickText = (root, selectors) => {
    for (const sel of selectors) {
      const el = root.querySelector(sel);
      if (el) {
        const t = (el.innerText || el.textContent || '').trim();
        if (t) return t;
      }
    }
    return '';
  };
  return Array.from(document.querySelectorAll(args.itemSelector)).map((item) => {
    const row = {};
    for (const [key, selectors] of Object.entries(args.fields)) {
      row[key] = pickText(item, selectors);
    }
    return row;
  });
}
"""


def _dom_selector_args() -> dict:
    """构造批量抽取脚本的选择器入参（与 selectors.py 保持同源）。"""
    return {
        "itemSelector": S.COMMENT_ITEM,
        "fields": {
            "content": [S.COMMENT_DETAIL],
            "username": [S.USER_NAME],
            "time": [S.COMMENT_TIME],
            "score": [S.SCORE, *S.SCORE_FALLBACK],
        },
    }


def _pick_best_sight(candidates: list[dict], query: str) -> dict:
    """
    从搜索结果中挑出与查询词最相关且具有真实游客点评的官方景点。

    打分规则：
    1. 名称完全相等（忽略大小写与空格）-> 最高分 100
    2. 名称包含查询词 / 查询词包含名称 -> 较高分 60~70
    3. 字符重合度 -> 兜底分
    4. 真实评论与活跃度加权：真实有评论加 25 分，暂无评论/0评论扣 35 分（避免匹配到0评论的冷门小院）
    5. 官方综合评分加权：有评分加 10 分
    """
    if not candidates:
        raise RuntimeError("没有可用的候选景点。")

    q = _normalize(query)
    best = None
    best_score = -999.0

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

        count_text = cand.get("count") or ""
        # 真实评论活跃度加权：真实有评论加 25 分，暂无评论扣 35 分
        if "暂无评论" in count_text or count_text == "0条评论":
            score -= 35.0
        elif count_text and re.search(r"\d+", count_text):
            score += 25.0

        if cand.get("score"):
            score += 10.0

        # 同分时保留靠前的结果
        if score > best_score:
            best_score = score
            best = cand

    return best or candidates[0]


def _normalize(text: str) -> str:
    """归一化文本用于比较：去空白、转小写。"""
    return re.sub(r"\s+", "", (text or "")).lower()
