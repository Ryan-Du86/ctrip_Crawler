"""导出模块测试"""

import json
import sqlite3

import pandas as pd
import pytest

from ctrip_crawler.exporter import DataExporter, detect_format


@pytest.fixture
def records():
    return [
        {"景区名称": "故宫", "用户名": "alice", "文本内容": "很好玩", "字数": 3},
        {"景区名称": "西湖", "用户名": "bob", "文本内容": "风景不错", "字数": 4},
    ]


def test_export_csv(records, tmp_path):
    out = tmp_path / "out.csv"
    files = DataExporter.export(records, str(out), "csv")
    assert files == [str(out)]
    df = pd.read_csv(out, encoding="utf-8-sig")
    assert len(df) == 2
    # 序号列自动补齐
    assert list(df["序号"]) == [1, 2]


def test_export_xlsx(records, tmp_path):
    out = tmp_path / "out.xlsx"
    DataExporter.export(records, str(out), "excel")
    df = pd.read_excel(out, engine="openpyxl")
    assert len(df) == 2
    assert "序号" in df.columns


def test_export_legacy_xls(records, tmp_path):
    """pandas 3.x 移除了 xlwt 引擎，这里走自实现的 xlwt 写入路径。"""
    out = tmp_path / "out.xls"
    DataExporter.export(records, str(out), "excel")
    assert out.exists()
    df = pd.read_excel(out, engine="calamine")
    assert len(df) == 2
    assert df["景区名称"].tolist() == ["故宫", "西湖"]


def test_export_json(records, tmp_path):
    out = tmp_path / "out.json"
    DataExporter.export(records, str(out), "json")
    data = json.loads(out.read_text(encoding="utf-8"))
    assert len(data) == 2
    assert data[0]["景区名称"] == "故宫"


def test_export_all_formats(records, tmp_path):
    base = tmp_path / "all"
    files = DataExporter.export(records, str(base), "all")
    assert len(files) == 4
    for suffix in (".csv", ".xlsx", ".db", ".json"):
        assert (tmp_path / f"all{suffix}").exists()


def test_sqlite_does_not_duplicate_on_rerun(records, tmp_path):
    """重复导出同一批数据不应产生重复行。"""
    out = tmp_path / "out.db"
    DataExporter.export(records, str(out), "sqlite")
    DataExporter.export(records, str(out), "sqlite")

    with sqlite3.connect(out) as conn:
        rows = pd.read_sql_query("SELECT * FROM ctrip_comments", conn)
    assert len(rows) == 2


def test_sqlite_overwrite_replaces(records, tmp_path):
    out = tmp_path / "out.db"
    DataExporter.export(records, str(out), "sqlite")
    DataExporter.export(records[:1], str(out), "sqlite", overwrite=True)

    with sqlite3.connect(out) as conn:
        rows = pd.read_sql_query("SELECT * FROM ctrip_comments", conn)
    assert len(rows) == 1


def test_export_empty_records(tmp_path):
    assert DataExporter.export([], str(tmp_path / "x.csv"), "csv") == []


def test_detect_format():
    assert detect_format("a.xlsx") == "excel"
    assert detect_format("a.xls") == "excel"
    assert detect_format("a.csv") == "csv"
    assert detect_format("a.db") == "sqlite"
    assert detect_format("a.json") == "json"
    assert detect_format("a.txt") == "csv"
    assert detect_format("a.xlsx", "csv") == "csv"
