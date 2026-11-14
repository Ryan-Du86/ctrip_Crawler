"""爬虫核心逻辑测试（不涉及浏览器）"""

import pandas as pd
import pytest

from ctrip_crawler.crawler import CtripSightCrawler, _normalize, _pick_best_sight


def test_load_spots_from_xlsx_named_column(tmp_path):
    path = tmp_path / "spots.xlsx"
    pd.DataFrame({"景点名称": ["故宫", "西湖"]}).to_excel(path, index=False)
    assert CtripSightCrawler.load_spots_from_file(str(path)) == ["故宫", "西湖"]


def test_load_spots_from_xlsx_first_column_fallback(tmp_path):
    path = tmp_path / "spots.xlsx"
    pd.DataFrame({"随便什么列": ["故宫", "西湖"]}).to_excel(path, index=False)
    assert CtripSightCrawler.load_spots_from_file(str(path)) == ["故宫", "西湖"]


def test_load_spots_deduplicates(tmp_path):
    path = tmp_path / "spots.xlsx"
    pd.DataFrame({"景点": ["故宫", "西湖", "故宫"]}).to_excel(path, index=False)
    assert CtripSightCrawler.load_spots_from_file(str(path)) == ["故宫", "西湖"]


def test_load_spots_from_csv(tmp_path):
    path = tmp_path / "spots.csv"
    path.write_text("景点\n故宫\n西湖\n", encoding="utf-8-sig")
    assert CtripSightCrawler.load_spots_from_file(str(path)) == ["故宫", "西湖"]


def test_load_spots_empty_sheet_raises(tmp_path):
    """空表格此前会抛裸 IndexError，现在应为清晰的 ValueError。"""
    path = tmp_path / "empty.xlsx"
    pd.DataFrame().to_excel(path, index=False)
    with pytest.raises(ValueError):
        CtripSightCrawler.load_spots_from_file(str(path))


def test_load_spots_missing_file():
    with pytest.raises(FileNotFoundError):
        CtripSightCrawler.load_spots_from_file("不存在的文件.xlsx")


def test_load_spots_unsupported_extension(tmp_path):
    path = tmp_path / "spots.txt"
    path.write_text("故宫", encoding="utf-8")
    with pytest.raises(ValueError):
        CtripSightCrawler.load_spots_from_file(str(path))


def test_pick_best_sight_prefers_exact_match():
    candidates = [
        {"name": "北京故宫博物院", "url": "a"},
        {"name": "故宫", "url": "b"},
        {"name": "西湖", "url": "c"},
    ]
    assert _pick_best_sight(candidates, "故宫")["url"] == "b"


def test_pick_best_sight_prefers_exact_over_contains():
    """完全相等应优先于「包含」匹配。"""
    candidates = [
        {"name": "杭州西湖", "url": "a"},
        {"name": "西湖", "url": "b"},
    ]
    assert _pick_best_sight(candidates, "西湖")["url"] == "b"


def test_pick_best_sight_prefers_contains_when_no_exact():
    """没有完全匹配时，名称被查询词包含的结果应排在无关结果前面。"""
    candidates = [
        {"name": "灵隐寺", "url": "a"},
        {"name": "西湖", "url": "b"},
    ]
    assert _pick_best_sight(candidates, "西湖风景名胜区")["url"] == "b"


def test_pick_best_sight_handles_empty_list():
    with pytest.raises(RuntimeError):
        _pick_best_sight([], "故宫")


def test_normalize():
    assert _normalize(" 故 宫 ") == "故宫"
    assert _normalize("Gugong") == "gugong"
    assert _normalize("") == ""


def test_normalize_spot_list_deduplicates():
    """run() 应对名单去重，避免同一景点被重复请求。"""
    assert CtripSightCrawler.normalize_spot_list(["故宫", "故宫", "西湖"]) == ["故宫", "西湖"]
    assert CtripSightCrawler.normalize_spot_list("故宫") == ["故宫"]
    assert CtripSightCrawler.normalize_spot_list(["故宫", "", "  "]) == ["故宫"]
