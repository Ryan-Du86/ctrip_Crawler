"""
别名映射表测试：数据文件可加载、查得到、提示文案齐全。
"""

from ctrip_crawler import aliases


def test_alias_map_is_not_empty():
    assert len(aliases.alias_map()) > 0


def test_find_alias_known_key():
    hit = aliases.find_alias("故宫")
    assert hit is not None
    key, url = hit
    assert key == "故宫"
    assert url.startswith("https://you.ctrip.com/sight/")


def test_find_alias_unknown_key_returns_none():
    assert aliases.find_alias("这个景点一定不存在") is None


def test_find_alias_empty_input_returns_none():
    assert aliases.find_alias("") is None
    assert aliases.find_alias("   ") is None


def test_find_alias_strips_whitespace():
    assert aliases.find_alias("  故宫  ") is not None


def test_every_alias_url_points_at_a_sight_page():
    for key, url in aliases.alias_map().items():
        assert "you.ctrip.com/sight/" in url, f"{key} -> {url}"


def test_get_hint_known_key():
    hint = aliases.get_hint("洒金桥")
    assert hint
    assert "回民街" in hint


def test_get_hint_unknown_key_returns_empty_string():
    assert aliases.get_hint("不存在的景点") == ""
    assert aliases.get_hint(None) == ""


def test_alias_map_returns_a_copy():
    """返回副本，调用方修改不会污染内部缓存。"""
    first = aliases.alias_map()
    first["临时键"] = "https://example.com"
    assert "临时键" not in aliases.alias_map()
