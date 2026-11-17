"""
路径解析测试。
"""

from pathlib import Path

from ctrip_crawler.paths import OUTPUT_DIR, resolve_output_path


def test_absolute_path_is_returned_as_is():
    target = str(Path("C:/tmp/data.xlsx"))
    assert resolve_output_path(target) == Path(target)


def test_relative_path_resolves_under_output_dir():
    resolved = resolve_output_path("reviews.xlsx")
    assert resolved == OUTPUT_DIR / "reviews.xlsx"
    assert resolved.is_absolute()


def test_nested_relative_path_stays_under_output_dir():
    resolved = resolve_output_path("sub/dir/reviews.xlsx")
    assert resolved == OUTPUT_DIR / "sub" / "dir" / "reviews.xlsx"
