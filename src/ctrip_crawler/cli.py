"""
命令行交互入口
"""

import argparse
import asyncio
import logging

from .crawler import CtripSightCrawler
from .logging_setup import setup_logging


def build_parser() -> argparse.ArgumentParser:
    """构造参数解析器（单独抽出便于测试）。"""
    parser = argparse.ArgumentParser(
        description="携程（Ctrip）景点与用户点评智能采集工具",
        formatter_class=argparse.RawTextHelpFormatter,
    )

    parser.add_argument(
        "spot_arg",
        nargs="?",
        default=None,
        help="待采集的景点名称、目的地或直接 URL (例如: '故宫博物院' 或 '武汉东湖' 或 URL)",
    )
    parser.add_argument(
        "--spot",
        "-s",
        type=str,
        default=None,
        help="待采集的景点名称、目的地或直接 URL",
    )
    parser.add_argument(
        "--batch",
        "-b",
        type=str,
        default=None,
        help="批量导入文件路径，支持 Excel (.xlsx, .xls) 或 CSV (.csv)",
    )
    parser.add_argument(
        "--pages",
        "-p",
        type=int,
        default=3,
        help="每个景点的最大翻页采集数量（每页 10 条，默认: 3）",
    )
    parser.add_argument(
        "--format",
        "-f",
        type=str,
        default="auto",
        choices=["auto", "excel", "sqlite", "csv", "json", "all"],
        help="导出格式选项：auto (由后缀决定) | excel | sqlite | csv | json | all (全部格式导出)",
    )
    parser.add_argument(
        "--output",
        "-o",
        type=str,
        default="携程点评采集结果.xlsx",
        help="输出文件名或路径 (默认: '携程点评采集结果.xlsx')",
    )
    parser.add_argument(
        "--no-cdp",
        action="store_true",
        help="禁用 CDP 模式（默认已开启 CDP，防封效果最好）",
    )
    parser.add_argument(
        "--cdp-port",
        type=int,
        default=9222,
        help="CDP 远程调试端口 (默认: 9222)",
    )
    parser.add_argument(
        "--no-headless",
        action="store_true",
        help="显示真实浏览器运行窗口（默认无头后台运行）",
    )
    parser.add_argument(
        "--log-file",
        type=str,
        default=None,
        help="将运行日志同时写入指定文件",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="输出 DEBUG 级别详细日志",
    )

    return parser


def main():
    parser = build_parser()
    args = parser.parse_args()

    setup_logging(
        level=logging.DEBUG if args.verbose else logging.INFO,
        log_file=args.log_file,
    )

    # 确定目标景点列表
    spot_target = args.spot or args.spot_arg

    if args.batch:
        # 从 Excel / CSV 批量加载
        try:
            spots = CtripSightCrawler.load_spots_from_file(args.batch)
        except Exception as exc:
            logging.getLogger(__name__).error("批量文件读取失败: %s", exc)
            raise SystemExit(1) from exc
    elif spot_target:
        spots = [spot_target]
    else:
        # 未输入任何参数时给出引导提示
        logging.getLogger(__name__).info("未提供景点名称或批量文件，默认使用示例: '故宫博物院'")
        spots = ["故宫博物院"]

    # 初始化采集器 (CDP 默认开启)
    crawler = CtripSightCrawler(
        use_cdp=not args.no_cdp,
        cdp_port=args.cdp_port,
        headless=not args.no_headless,
    )

    # 运行采集
    asyncio.run(
        crawler.run(
            spots=spots,
            max_pages=args.pages,
            output_file=args.output,
            format_type=args.format,
        )
    )


if __name__ == "__main__":
    main()
