"""
导出模块测试：覆盖四种格式、SQLite 去重追加、以及文件句柄释放。
"""

import json
import os
import sqlite3
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from ctrip_crawler.exporter import INDEX_COLUMN, DataExporter, _json_default

RECORDS = [
    {"景区名称": "故宫", "用户名": "u1", "评分": "5分", "文本内容": "很好"},
    {"景区名称": "故宫", "用户名": "u2", "评分": "4分", "文本内容": "不错"},
]


@pytest.fixture(autouse=True)
def _redirect_output(monkeypatch, tmp_path):
    """把所有导出产物重定向到临时目录，避免污染真实的 output/。"""
    monkeypatch.setattr(
        "ctrip_crawler.exporter.resolve_output_path",
        lambda p: Path(p) if Path(p).is_absolute() else tmp_path / p,
    )


def _read_table(path: Path, table: str = "ctrip_comments") -> list[tuple]:
    conn = sqlite3.connect(path)
    try:
        return conn.execute(f'SELECT * FROM "{table}"').fetchall()
    finally:
        conn.close()


def test_export_empty_records_returns_empty_list():
    assert DataExporter.export([], "empty.csv", format_type="csv") == []


def test_export_json_contains_index_column(tmp_path):
    files = DataExporter.export(RECORDS, "out.json", format_type="json")
    assert len(files) == 1

    with open(files[0], encoding="utf-8") as f:
        payload = json.load(f)

    assert len(payload) == 2
    assert INDEX_COLUMN in payload[0]
    assert payload[0][INDEX_COLUMN] == 1
    assert payload[0]["文本内容"] == "很好"


def test_export_json_survives_numpy_scalars(tmp_path):
    """numpy 标量不能被 json.dump 直接序列化，需要 default 兜底。"""
    df = pd.DataFrame({"序号": np.array([1, 2], dtype=np.int64), "值": ["a", "b"]})
    path = tmp_path / "np.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(df.to_dict(orient="records"), f, ensure_ascii=False, default=_json_default)

    with open(path, encoding="utf-8") as f:
        payload = json.load(f)
    assert payload[0]["序号"] == 1


def test_json_default_falls_back_to_str():
    assert _json_default(np.int64(7)) == 7
    assert _json_default(object()) .startswith("<object")


def test_export_csv_uses_utf8_bom(tmp_path):
    files = DataExporter.export(RECORDS, "out.csv", format_type="csv")
    raw = Path(files[0]).read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf")

    df = pd.read_csv(files[0], encoding="utf-8-sig")
    assert len(df) == 2
    assert INDEX_COLUMN in df.columns


def test_export_excel(tmp_path):
    files = DataExporter.export(RECORDS, "out.xlsx", format_type="excel")
    assert files[0].endswith(".xlsx")

    df = pd.read_excel(files[0], engine="openpyxl")
    assert len(df) == 2
    assert list(df.columns)[0] == INDEX_COLUMN


def test_export_all_formats(tmp_path):
    files = DataExporter.export(RECORDS, "out", format_type="all")
    suffixes = {Path(f).suffix for f in files}
    assert suffixes == {".csv", ".xlsx", ".db", ".json"}


def test_export_sqlite_releases_file_handle(tmp_path):
    """回归测试：导出后 .db 文件不应再被占用，否则 Windows 上无法覆盖导出。"""
    files = DataExporter.export(RECORDS, "handle.db", format_type="sqlite")
    path = Path(files[0])

    # 文件仍存在，但必须已经可以被删除（说明连接已关闭）
    assert path.exists()
    os.remove(path)
    assert not path.exists()


def test_export_sqlite_appends_without_duplicates(tmp_path):
    files = DataExporter.export(RECORDS, "dedup.db", format_type="sqlite")
    assert len(_read_table(Path(files[0]))) == 2

    # 同样的数据再导一次，不应该产生重复行
    DataExporter.export(RECORDS, "dedup.db", format_type="sqlite")
    assert len(_read_table(Path(files[0]))) == 2


def test_export_sqlite_appends_only_new_rows(tmp_path):
    files = DataExporter.export(RECORDS, "grow.db", format_type="sqlite")
    path = Path(files[0])

    extended = RECORDS + [{"景区名称": "西湖", "用户名": "u3", "评分": "5分", "文本内容": "很棒"}]
    DataExporter.export(extended, "grow.db", format_type="sqlite")

    rows = _read_table(path)
    assert len(rows) == 3


def test_export_sqlite_overwrite_replaces_table(tmp_path):
    files = DataExporter.export(RECORDS, "ow.db", format_type="sqlite")
    DataExporter.export(RECORDS[:1], "ow.db", format_type="sqlite", overwrite=True)
    assert len(_read_table(Path(files[0]))) == 1


def test_auto_format_infers_from_suffix(tmp_path):
    files = DataExporter.export(RECORDS, "auto.json", format_type="auto")
    assert files[0].endswith(".json")

    files = DataExporter.export(RECORDS, "auto.db", format_type="auto")
    assert files[0].endswith(".db")


def test_auto_format_falls_back_to_csv(tmp_path):
    files = DataExporter.export(RECORDS, "auto.unknown", format_type="auto")
    assert files[0].endswith(".csv")
