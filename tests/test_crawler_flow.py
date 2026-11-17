"""
采集主流程的行为测试：数据落盘时机与标签页选取策略。

这些用例不启动真实浏览器，只验证流程编排本身。
"""

import asyncio

import pytest

from ctrip_crawler.crawler import CtripSightCrawler


class _FakePage:
    def __init__(self, url: str = ""):
        self.url = url


class _FakeContext:
    def __init__(self, pages: list[_FakePage]):
        self.pages = pages
        self.created = 0

    async def new_page(self) -> _FakePage:
        self.created += 1
        page = _FakePage()
        self.pages.append(page)
        return page


class TestExportAlwaysRuns:
    """回归测试：采集阶段失败时，已采到的数据也必须落盘。"""

    def test_export_runs_when_crawl_raises(self, monkeypatch, tmp_path):
        crawler = CtripSightCrawler()
        crawler.records = [{"景区名称": "故宫", "文本内容": "已采集"}]

        exported: dict = {}

        def _fake_export(output_file, format_type):
            exported["called"] = True
            exported["records"] = list(crawler.records)

        async def _boom(*_args, **_kwargs):
            raise RuntimeError("CDP connection failed")

        monkeypatch.setattr(crawler, "_run_crawl", _boom)
        monkeypatch.setattr(crawler, "_export", _fake_export)

        with pytest.raises(RuntimeError):
            asyncio.run(crawler.run(["故宫"], output_file=str(tmp_path / "out.xlsx")))

        assert exported.get("called") is True
        assert exported["records"][0]["景区名称"] == "故宫"

    def test_export_runs_on_success(self, monkeypatch, tmp_path):
        crawler = CtripSightCrawler()
        exported: dict = {}

        async def _ok(*_args, **_kwargs):
            crawler.records.append({"景区名称": "西湖", "文本内容": "好"})

        monkeypatch.setattr(crawler, "_run_crawl", _ok)
        monkeypatch.setattr(
            crawler, "_export", lambda o, f: exported.update(called=True, output=o)
        )

        asyncio.run(crawler.run(["西湖"], output_file=str(tmp_path / "out.xlsx")))

        assert exported.get("called") is True
        assert exported["output"] == str(tmp_path / "out.xlsx")

    def test_empty_spot_list_skips_crawl_and_export(self, monkeypatch, tmp_path):
        """空名单没有任何数据，既不采集也不导出。"""
        crawler = CtripSightCrawler()
        called = {"crawl": False, "export": False}

        async def _crawl(*_args, **_kwargs):
            called["crawl"] = True

        monkeypatch.setattr(crawler, "_run_crawl", _crawl)
        monkeypatch.setattr(crawler, "_export", lambda o, f: called.update(export=True))

        asyncio.run(crawler.run([]))

        assert called == {"crawl": False, "export": False}


class TestPickTargetPage:
    """CDP 模式下不应随意占用用户正在使用的标签页。"""

    def test_prefers_tab_already_on_a_sight_page(self):
        ctx = _FakeContext(
            [
                _FakePage("https://example.com/news"),
                _FakePage("https://you.ctrip.com/sight/1/229.html"),
            ]
        )
        page = asyncio.run(CtripSightCrawler._pick_target_page(ctx))
        assert page.url == "https://you.ctrip.com/sight/1/229.html"
        assert ctx.created == 0

    def test_falls_back_to_first_open_tab(self):
        ctx = _FakeContext([_FakePage("https://example.com/news"), _FakePage("https://other.com")])
        page = asyncio.run(CtripSightCrawler._pick_target_page(ctx))
        assert page.url == "https://example.com/news"
        assert ctx.created == 0

    def test_opens_new_page_when_no_tabs(self):
        ctx = _FakeContext([])
        asyncio.run(CtripSightCrawler._pick_target_page(ctx))
        assert ctx.created == 1

    def test_ignores_non_ctrip_sight_tabs(self):
        """商城页之类的 /sight/ 路径不算景点页。"""
        ctx = _FakeContext([_FakePage("https://mall.ctrip.com/sight/1/2.html")])
        page = asyncio.run(CtripSightCrawler._pick_target_page(ctx))
        assert page.url == "https://mall.ctrip.com/sight/1/2.html"
        assert ctx.created == 0
