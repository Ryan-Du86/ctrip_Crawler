# -*- coding: utf-8 -*-
"""
携程（Ctrip）景点与用户点评智能采集引擎
特性：
1. 默认开启 CDP（支持自动接管与自动拉起本地 Chrome/Edge 调试端口）
2. 智能检索：直接输入目的地/景点名即可自动解析并导航至点评主页
3. 批量采集：支持从 Excel (.xlsx/.xls) 或 CSV 文件中导入景点名单批量爬取
4. 多格式输出：无缝支持 CSV, Excel, SQLite, JSON 以及多格式同步导出
"""

import asyncio
import os
import random
import re
from typing import Dict, List, Optional, Tuple, Union
from urllib.parse import quote
import pandas as pd
from playwright.async_api import async_playwright, Page, BrowserContext

from .browser_manager import ensure_cdp_browser
from .exporter import DataExporter


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
        self.records: List[Dict] = []
        self.seen_keys = set()
        self._browser_proc = None

    @staticmethod
    def load_spots_from_file(file_path: str) -> List[str]:
        """从 Excel 或 CSV 文件中自动提取景点/目的地名称列表"""
        if not os.path.exists(file_path):
            raise FileNotFoundError(f"找不到输入的批量文件: {file_path}")

        ext = os.path.splitext(file_path)[1].lower()
        if ext in (".xlsx", ".xls"):
            df = pd.read_excel(file_path)
        elif ext == ".csv":
            try:
                df = pd.read_csv(file_path, encoding="utf-8-sig")
            except Exception:
                df = pd.read_csv(file_path, encoding="gbk")
        else:
            raise ValueError(f"不支持的文件格式: {ext}，仅支持 .xlsx, .xls, .csv")

        # 优先匹配常见的列名
        target_col = None
        candidate_cols = [
            "景点", "景区", "景点名称", "景区名称", "目的地", "名称", 
            "关键词", "spot", "scenic_spot", "name", "keyword"
        ]
        for col in df.columns:
            if str(col).strip().lower() in candidate_cols:
                target_col = col
                break

        # 如果没有匹配到列名，默认使用第一列
        if target_col is None:
            target_col = df.columns[0]
            print(f"[*] 未在表格中发现标准列名，默认使用首列 [{target_col}] 作为景点名单。")
        else:
            print(f"[√] 成功识别景点所在列: [{target_col}]")

        spots = [str(x).strip() for x in df[target_col].dropna().tolist() if str(x).strip()]
        print(f"[*] 从文件读取到 {len(spots)} 个待采集景点: {spots[:5]}{'...' if len(spots) > 5 else ''}")
        return spots

    async def _resolve_spot_url(self, page: Page, spot_input: str) -> Tuple[str, str]:
        """
        智能解析输入：
        - 若已是 URL，直接返回；
        - 若是名称/目的地，通过携程全局搜索 API 自动检索匹配最佳景点点评页面
        """
        # 0. 优先检测：若当前标签页正好就是携程景点/点评页，直接复用当前页面！
        current_url = page.url or ""
        if "you.ctrip.com/sight/" in current_url:
            # 如果未传景点名，或者当前页标题包含该景点，直接复用
            page_title = await page.title()
            if not spot_input or spot_input in page_title or spot_input in current_url or spot_input == "当前页面":
                print(f"[√] 发现当前浏览器正停留在目标景点页面: 【{page_title}】")
                print(f"    无需重复搜索，直接在当前标签页开始采集！")
                return current_url, page_title.split("游玩攻略")[0].strip()

        spot_input = spot_input.strip()
        if spot_input.startswith("http://") or spot_input.startswith("https://"):
            return spot_input, "指定景点"

        print(f"[*] 正在智能检索目的地/景点: [{spot_input}] ...")
        search_url = f"https://you.ctrip.com/globalsearch/?keyword={quote(spot_input)}"
        
        captured_sights = []

        async def _on_search_response(resp):
            if "getGsOnlineResult" in resp.url:
                try:
                    data = await resp.json()
                    for home_item in data.get("homeItems", []):
                        for card in home_item.get("items", []):
                            u = card.get("url") or ""
                            if "/sight/" in u:
                                captured_sights.append({
                                    "name": card.get("word") or card.get("name") or spot_input,
                                    "url": u,
                                    "score": card.get("commentScoreText") or "",
                                    "count": card.get("commentCountText") or "",
                                })
                except Exception:
                    pass

        page.on("response", _on_search_response)
        try:
            await page.goto(search_url, wait_until="networkidle", timeout=45000)
            await asyncio.sleep(2.5)
        except Exception as e:
            print(f"  [!] 访问搜索页异常: {e}")

        # 优先使用 API 捕获的 sight 卡片
        if captured_sights:
            best = captured_sights[0]
            print(f"[√] 智能检索成功！匹配到: 【{best['name']}】({best['score']}) -> {best['url']}")
            return best["url"], best["name"]

        # 兜底：DOM 中直接查找 a[href*="/sight/"]
        sight_links = await page.locator('a[href*="/sight/"]').all()
        for link in sight_links:
            href = await link.get_attribute("href")
            text = (await link.inner_text()).strip().replace("\n", " ")
            if href and text and not href.startswith("javascript"):
                print(f"[√] 从页面卡片检索到: 【{text}】 -> {href}")
                return href, text

        raise RuntimeError(f"未能自动检索到景点 [{spot_input}] 对应的携程点评主页，请检查名称是否准确。")

    async def _extract_page_comments(self, page: Page, spot_name: str) -> int:
        """从当前页点评卡片中提取数据"""
        items = await page.locator(".commentList > .commentItem").all()
        new_count = 0

        for item in items:
            # 1. 评论正文
            detail_loc = item.locator(".commentDetail")
            if not await detail_loc.count():
                continue
            content = await detail_loc.inner_text()
            content = content.strip().replace("\r", " ").replace("\n", " ")
            if not content:
                continue

            # 2. 用户名
            user_loc = item.locator(".baseInfo .userName")
            user_name = (await user_loc.inner_text()).strip() if await user_loc.count() else "匿名用户"

            # 3. 时间与 IP 属地
            time_loc = item.locator(".commentTime")
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
            score_loc = item.locator(".scoreInfo .score")
            score = (await score_loc.inner_text()).strip() if await score_loc.count() else ""

            # 去重
            unique_key = f"{spot_name}_{user_name}_{content[:25]}"
            if unique_key in self.seen_keys:
                continue

            self.seen_keys.add(unique_key)
            new_count += 1

            self.records.append({
                "景区名称": spot_name,
                "平台": "携程",
                "内容类型": "用户点评",
                "时间": publish_time or time_raw,
                "文本内容": content,
                "字数": len(content),
                "评分": score,
                "IP属地": ip_location,
                "用户名": user_name,
            })
            print(f"    [+] 采集点评: [{publish_time or '未知时间'}] {user_name} ({score}): {content[:35]}...")

        return new_count

    async def crawl_single(
        self,
        page: Page,
        spot_input: str,
        max_pages: int = 3,
    ) -> int:
        """采集单个景点的评论数据"""
        try:
            target_url, spot_name = await self._resolve_spot_url(page, spot_input)
        except Exception as e:
            print(f"[!] 解析景点 [{spot_input}] 失败: {e}")
            return 0

        print(f"[*] 导航至景点点评主页: {target_url}")
        try:
            await page.goto(target_url, wait_until="networkidle", timeout=60000)
        except Exception as e:
            print(f"[!] 打开主页超时/异常: {e}")

        # 定位评论容器
        comment_loc = page.locator(".commentList")
        try:
            await comment_loc.wait_for(state="attached", timeout=15000)
            await comment_loc.scroll_into_view_if_needed()
            await asyncio.sleep(1.5)
        except Exception:
            print(f"  [!] 未能定位到点评列表 (.commentList)，该景点可能暂无用户评论。")
            return 0

        spot_start_count = len(self.records)

        for current_page in range(1, max_pages + 1):
            print(f"  [>>> {spot_name} - 正在采集第 {current_page}/{max_pages} 页 <<<]")
            new_added = await self._extract_page_comments(page, spot_name)
            print(f"    [-] 本页新增: {new_added} 条 (该景点累计: {len(self.records) - spot_start_count} 条)")

            if current_page >= max_pages:
                break

            # 翻页
            next_btn = page.locator(".ant-pagination-next")
            if not await next_btn.count():
                print("    [*] 未找到下一页控件，已到末页。")
                break

            btn_class = await next_btn.get_attribute("class") or ""
            if "ant-pagination-disabled" in btn_class:
                print("    [*] 下一页按钮已禁用 (已无更多点评)。")
                break

            first_text = ""
            first_detail = page.locator(".commentList > .commentItem .commentDetail").first
            if await first_detail.count():
                first_text = (await first_detail.inner_text()).strip()

            await next_btn.scroll_into_view_if_needed()
            await next_btn.click()

            # 等待刷新
            refreshed = False
            for _ in range(20):
                await asyncio.sleep(0.5)
                new_detail = page.locator(".commentList > .commentItem .commentDetail").first
                if await new_detail.count():
                    cur_text = (await new_detail.inner_text()).strip()
                    if cur_text and cur_text != first_text:
                        refreshed = True
                        break

            if not refreshed:
                await asyncio.sleep(2)

            await asyncio.sleep(random.uniform(1.5, 3.0))

        return len(self.records) - spot_start_count

    async def run(
        self,
        spots: Union[str, List[str]],
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
        if isinstance(spots, str):
            spot_list = [spots]
        else:
            spot_list = list(spots)

        if not spot_list:
            print("[!] 采集名单为空，任务终止。")
            return

        async with async_playwright() as p:
            browser = None
            page: Page = None

            if self.use_cdp:
                endpoint, proc = ensure_cdp_browser(
                    port=self.cdp_port,
                    headless=self.headless,
                )
                self._browser_proc = proc
                try:
                    browser = await p.chromium.connect_over_cdp(endpoint)
                    context = browser.contexts[0] if browser.contexts else await browser.new_context()
                    # 优先复用当前已经打开的标签页，绝不重复新开！
                    if context.pages:
                        page = context.pages[-1]
                        print(f"[*] 成功接管当前打开的浏览器标签页: {page.url or '空白页'}")
                    else:
                        page = await context.new_page()
                except Exception as e:
                    print(f"[!] 连接 CDP 浏览器失败: {e}")
                    return
            else:
                print(f"[*] 运行模式: 原生自动化驱动 (headless={self.headless})")
                for ch in ["msedge", "chrome", None]:
                    try:
                        browser = await p.chromium.launch(channel=ch, headless=self.headless)
                        print(f"  [√] 成功启动浏览器内核: {ch or 'Playwright Chromium'}")
                        break
                    except Exception:
                        continue
                if not browser:
                    raise RuntimeError("无法启动浏览器，请安装 Edge/Chrome。")
                page = await browser.new_page()

            # 遍历采集
            total_spots = len(spot_list)
            for idx, spot_item in enumerate(spot_list, start=1):
                print("\n" + "#" * 65)
                print(f"[*] [任务进度 {idx}/{total_spots}] 准备处理景点: {spot_item}")
                print("#" * 65)

                await self.crawl_single(page, spot_item, max_pages=max_pages)

                # 批量任务间友好随机等待
                if idx < total_spots:
                    interval = random.uniform(2.5, 4.5)
                    print(f"[*] 景点间间隔休眠 {interval:.1f} 秒，防止触发平台风控...")
                    await asyncio.sleep(interval)

            if not self.use_cdp:
                await page.close()
                if browser:
                    await browser.close()
            else:
                print("[*] CDP 模式：已保留当前浏览器与页面窗口，未执行关闭。")

        # 导出结果
        DataExporter.export(
            records=self.records,
            output_path=output_file,
            format_type=format_type,
        )
