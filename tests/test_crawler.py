"""
爬虫核心纯函数测试：候选景点打分、文本归一化、URL 校验、名单去重。
"""

import pytest

from ctrip_crawler.crawler import (
    CtripSightCrawler,
    _dom_selector_args,
    _normalize,
    _official_meta_args,
    _pick_best_sight,
)


class TestNormalize:
    def test_removes_whitespace(self):
        assert _normalize(" 故 宫 ") == "故宫"

    def test_lowercases(self):
        assert _normalize("Gugong") == "gugong"

    def test_handles_empty_and_none(self):
        assert _normalize("") == ""
        assert _normalize(None) == ""


class TestPickBestSight:
    def test_exact_name_wins(self):
        candidates = [
            {"name": "西湖", "url": "https://you.ctrip.com/sight/a/1.html", "count": "100条评论"},
            {"name": "西湖", "url": "https://you.ctrip.com/sight/b/2.html", "count": "暂无评论"},
        ]
        best = _pick_best_sight(candidates, "西湖")
        # 同名时，有真实评论的那个应当胜出
        assert best["url"].endswith("1.html")

    def test_prefers_candidate_with_reviews(self):
        candidates = [
            {"name": "某某小院", "url": "https://you.ctrip.com/sight/a/1.html", "count": "暂无评论"},
            {"name": "某某小院", "url": "https://you.ctrip.com/sight/b/2.html", "count": "88条评论"},
        ]
        best = _pick_best_sight(candidates, "某某小院")
        assert best["url"].endswith("2.html")

    def test_penalises_zero_reviews(self):
        with_reviews = _pick_best_sight(
            [
                {"name": "无关景点", "url": "https://you.ctrip.com/sight/a/1.html", "count": "500条评论"},
                {"name": "冷门小院", "url": "https://you.ctrip.com/sight/b/2.html", "count": "0条评论"},
            ],
            "冷门小院",
        )
        assert with_reviews["name"] == "冷门小院"

    def test_raises_on_empty_candidate_list(self):
        with pytest.raises(RuntimeError):
            _pick_best_sight([], "故宫")


class TestIsSightUrl:
    @pytest.mark.parametrize(
        "url",
        [
            "https://you.ctrip.com/sight/1/229.html",
            "https://you.ctrip.com/sight/hangzhou14/2555.html",
        ],
    )
    def test_accepts_sight_pages(self, url):
        assert CtripSightCrawler._is_sight_url(url) is True

    @pytest.mark.parametrize(
        "url",
        [
            "",
            "https://you.ctrip.com/thing/1.html",
            "https://mall.ctrip.com/sight/1/229.html",
            "https://example.com/sight/1/229.html",
        ],
    )
    def test_rejects_non_sight_pages(self, url):
        assert CtripSightCrawler._is_sight_url(url) is False


class TestNormalizeSpotList:
    def test_string_input_becomes_single_element_list(self):
        assert CtripSightCrawler.normalize_spot_list("故宫") == ["故宫"]

    def test_removes_blank_entries(self):
        assert CtripSightCrawler.normalize_spot_list(["故宫", "", "   "]) == ["故宫"]

    def test_deduplicates_preserving_order(self):
        assert CtripSightCrawler.normalize_spot_list(["西湖", "故宫", "西湖"]) == ["西湖", "故宫"]


class TestSelectorArgs:
    def test_score_selectors_keep_priority_order(self):
        """两条抽取路径必须按同一优先级解析评分字段。"""
        score_selectors = _dom_selector_args()["fields"]["score"]
        assert score_selectors[0] == ".scroreInfo .averageScore"
        # 兜底项展开成独立元素，而不是逗号拼接的一整个字符串
        assert ".scroreInfo" in score_selectors
        assert ".scoreInfo .score" in score_selectors
        assert not any("," in sel for sel in score_selectors)

    def test_official_meta_args_come_from_selectors_module(self):
        args = _official_meta_args()
        assert "h1" in args["officialName"]
        assert ".rankText" in args["rankTag"]
        assert args["score"]
