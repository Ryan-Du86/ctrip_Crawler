"""
多格式数据导出模块：支持 CSV, Excel(含 .xls), SQLite, JSON 及全部导出
"""

import json
import logging
import os
import sqlite3

import pandas as pd

from .paths import resolve_output_path

logger = logging.getLogger(__name__)

# 表格首列序号
INDEX_COLUMN = "序号"


class DataExporter:
    """负责把采集到的记录落盘为各种格式。"""

    @staticmethod
    def export(
        records: list[dict],
        output_path: str,
        format_type: str = "auto",
        table_name: str = "ctrip_comments",
        overwrite: bool = False,
    ) -> list[str]:
        """
        导出数据为指定格式

        :param records: 爬取到的字典列表
        :param output_path: 输出文件或基础路径
        :param format_type: auto | csv | excel | sqlite | json | all
        :param table_name: SQLite 表名
        :param overwrite: SQLite 导出时是否覆盖已有表。
                          False（默认）会按内容去重后追加，避免重复运行产生重复行。
        :return: 成功导出的文件绝对路径列表
        """
        if not records:
            logger.warning("No data to export.")
            return []

        df = pd.DataFrame(records)
        # 确保存在序号列
        if INDEX_COLUMN not in df.columns:
            df.insert(0, INDEX_COLUMN, range(1, len(df) + 1))

        output_path = str(resolve_output_path(output_path))
        base_dir = os.path.dirname(output_path)
        if base_dir:
            os.makedirs(base_dir, exist_ok=True)

        base_name, ext = os.path.splitext(output_path)
        ext = ext.lower()
        format_lower = format_type.lower()

        # 自动推断格式
        if format_lower == "auto":
            ext_map = {
                ".csv": "csv",
                ".xlsx": "excel",
                ".xls": "excel",
                ".db": "sqlite",
                ".sqlite": "sqlite",
                ".json": "json",
            }
            format_lower = ext_map.get(ext, "csv")

        exported_files: list[str] = []

        # 1. CSV
        if format_lower in ("csv", "all"):
            csv_path = (
                base_name + ".csv"
                if format_lower == "all"
                else (output_path if ext == ".csv" else base_name + ".csv")
            )
            df.to_csv(csv_path, index=False, encoding="utf-8-sig")
            exported_files.append(csv_path)

        # 2. Excel（.xlsx 用 openpyxl；.xls 由 xlwt 直写，pandas 3 已移除该引擎）
        if format_lower in ("excel", "all"):
            if format_lower == "all":
                excel_path = base_name + ".xlsx"
            elif ext in (".xlsx", ".xls"):
                excel_path = output_path
            else:
                excel_path = base_name + ".xlsx"
            DataExporter._write_excel(df, excel_path)
            exported_files.append(excel_path)

        # 3. SQLite
        if format_lower in ("sqlite", "all"):
            sqlite_path = (
                base_name + ".db"
                if format_lower == "all"
                else (output_path if ext in (".db", ".sqlite") else base_name + ".db")
            )
            DataExporter._write_sqlite(df, sqlite_path, table_name, overwrite=overwrite)
            exported_files.append(sqlite_path)

        # 4. JSON
        if format_lower in ("json", "all"):
            json_path = (
                base_name + ".json"
                if format_lower == "all"
                else (output_path if ext == ".json" else base_name + ".json")
            )
            # 转为标准 utf-8 字典列表
            data_dict = df.to_dict(orient="records")
            with open(json_path, "w", encoding="utf-8") as f:
                json.dump(data_dict, f, ensure_ascii=False, indent=2)
            exported_files.append(json_path)

        logger.info("Export complete. Saved %d review(s) to:", len(df))
        for path in exported_files:
            logger.info("    -> %s", path)

        return exported_files

    @staticmethod
    def _write_excel(df: pd.DataFrame, path: str) -> None:
        """写出 Excel 文件，按后缀选择引擎。"""
        ext = os.path.splitext(path)[1].lower()
        if ext == ".xls":
            DataExporter._write_legacy_xls(df, path)
        else:
            df.to_excel(path, index=False)

    @staticmethod
    def _write_legacy_xls(df: pd.DataFrame, path: str) -> None:
        """
        使用 xlwt 直接写出 .xls（BIFF8）文件。

        pandas 3.x 已移除 xlwt 写入引擎，而 xlrd >= 2.0 也已不再支持写 .xls，
        因此这里手写一份最小可用的 xlwt 导出实现。
        """
        try:
            import xlwt
        except ImportError as exc:  # pragma: no cover - 依赖缺失时的兜底提示
            raise RuntimeError("导出 .xls 需要 xlwt，请先执行: uv pip install xlwt") from exc

        workbook = xlwt.Workbook(encoding="utf-8")
        sheet = workbook.add_sheet("Sheet1")

        # 表头
        for col_idx, column in enumerate(df.columns):
            sheet.write(0, col_idx, str(column))

        # 数据行
        for row_idx, row in enumerate(df.itertuples(index=False, name=None), start=1):
            for col_idx, value in enumerate(row):
                if value is None:
                    continue
                if isinstance(value, bool):
                    sheet.write(row_idx, col_idx, "TRUE" if value else "FALSE")
                elif isinstance(value, (int, float)):
                    sheet.write(row_idx, col_idx, value)
                else:
                    # 控制 Excel 单元格最大长度 32767
                    text = str(value)
                    sheet.write(row_idx, col_idx, text[:32767])

        workbook.save(path)

    @staticmethod
    def _write_sqlite(
        df: pd.DataFrame,
        path: str,
        table_name: str,
        overwrite: bool = False,
    ) -> None:
        """
        写入 SQLite。

        默认（overwrite=False）时不会重复追加相同内容：若目标表已存在，
        先按内容列做差集过滤，只插入新增记录，避免重复运行产生重复行。
        """
        with sqlite3.connect(path) as conn:
            if overwrite:
                df.to_sql(table_name, conn, if_exists="replace", index=False)
                return

            existing = _read_table_columns(conn, table_name)
            if not existing:
                df.to_sql(table_name, conn, if_exists="append", index=False)
                return

            # 以目标表已有的列为准，缺失的列补空值，避免列数不一致导致写入失败
            common_cols = [c for c in df.columns if c in existing]
            if not common_cols:
                df.to_sql(table_name, conn, if_exists="append", index=False)
                return

            to_insert = df[common_cols]
            old_rows = pd.read_sql_query(f'SELECT * FROM "{table_name}"', conn)[common_cols]

            if not old_rows.empty:
                merged = pd.concat([old_rows, to_insert], ignore_index=True)
                deduped = merged.drop_duplicates()
                inserted = len(deduped) - len(old_rows)
                if inserted <= 0:
                    logger.info("Identical rows already exist in SQLite; nothing added (%s)", path)
                    return
                to_insert = deduped.iloc[len(old_rows) :]

            to_insert.to_sql(table_name, conn, if_exists="append", index=False)
            logger.info("Inserted %d new row(s) into SQLite (%s)", len(to_insert), path)


def _read_table_columns(conn: sqlite3.Connection, table_name: str) -> list[str]:
    """读取已存在表的列名，表不存在时返回空列表。"""
    try:
        cursor = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
            (table_name,),
        )
        if cursor.fetchone() is None:
            return []
        cursor = conn.execute(f'PRAGMA table_info("{table_name}")')
        return [row[1] for row in cursor.fetchall()]
    except sqlite3.Error:
        return []


def detect_format(output_path: str, format_type: str = "auto") -> str:
    """
    根据输出路径与用户指定的格式推导出最终格式名。

    :param output_path: 输出文件路径
    :param format_type: auto | csv | excel | sqlite | json | all
    :return: csv | excel | sqlite | json | all
    """
    if format_type.lower() != "auto":
        return format_type.lower()
    ext = os.path.splitext(output_path)[1].lower()
    return {
        ".csv": "csv",
        ".xlsx": "excel",
        ".xls": "excel",
        ".db": "sqlite",
        ".sqlite": "sqlite",
        ".json": "json",
    }.get(ext, "csv")
