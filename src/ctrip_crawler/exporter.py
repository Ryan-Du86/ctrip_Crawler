# -*- coding: utf-8 -*-
"""
多格式数据导出模块：支持 CSV, Excel, SQLite, JSON 及全部导出
"""

import json
import os
import sqlite3
from typing import Dict, List, Union
import pandas as pd


class DataExporter:
    @staticmethod
    def export(
        records: List[Dict],
        output_path: str,
        format_type: str = "auto",
        table_name: str = "ctrip_comments",
    ) -> List[str]:
        """
        导出数据为指定格式
        :param records: 爬取到的字典列表
        :param output_path: 输出文件或基础路径
        :param format_type: auto | csv | excel | sqlite | json | all
        :param table_name: SQLite 表名
        :return: 成功导出的文件绝对路径列表
        """
        if not records:
            print("[!] 没有可导出的数据。")
            return []

        df = pd.DataFrame(records)
        # 确保存在序号列
        if "序号" not in df.columns:
            df.insert(0, "序号", range(1, len(df) + 1))

        # 去除路径多余引号并规范化
        output_path = os.path.abspath(output_path)
        base_dir = os.path.dirname(output_path)
        if base_dir:
            os.makedirs(base_dir, exist_ok=True)

        base_name, ext = os.path.splitext(output_path)
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
            format_lower = ext_map.get(ext.lower(), "csv")

        exported_files = []

        # 1. CSV
        if format_lower in ("csv", "all"):
            csv_path = base_name + ".csv" if format_lower == "all" else (output_path if ext == ".csv" else base_name + ".csv")
            df.to_csv(csv_path, index=False, encoding="utf-8-sig")
            exported_files.append(csv_path)

        # 2. Excel
        if format_lower in ("excel", "all"):
            excel_path = base_name + ".xlsx" if format_lower == "all" else (output_path if ext in (".xlsx", ".xls") else base_name + ".xlsx")
            df.to_excel(excel_path, index=False)
            exported_files.append(excel_path)

        # 3. SQLite
        if format_lower in ("sqlite", "all"):
            sqlite_path = base_name + ".db" if format_lower == "all" else (output_path if ext in (".db", ".sqlite") else base_name + ".db")
            with sqlite3.connect(sqlite_path) as conn:
                df.to_sql(table_name, conn, if_exists="append", index=False)
            exported_files.append(sqlite_path)

        # 4. JSON
        if format_lower in ("json", "all"):
            json_path = base_name + ".json" if format_lower == "all" else (output_path if ext == ".json" else base_name + ".json")
            # 转为标准 utf-8 字典列表
            data_dict = df.to_dict(orient="records")
            with open(json_path, "w", encoding="utf-8") as f:
                json.dump(data_dict, f, ensure_ascii=False, indent=2)
            exported_files.append(json_path)

        print("\n" + "=" * 65)
        print(f"[√] 导出完成！共保存 {len(df)} 条点评记录，输出文件:")
        for f in exported_files:
            print(f"    -> {f}")
        print("=" * 65)

        return exported_files
