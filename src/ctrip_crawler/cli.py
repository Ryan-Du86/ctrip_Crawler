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
        description="Ctrip attraction review crawler",
        formatter_class=argparse.RawTextHelpFormatter,
    )

    parser.add_argument(
        "spot_arg",
        nargs="?",
        default=None,
        help="Attraction name, destination, or URL (positional). Example: 'Forbidden City'",
    )
    parser.add_argument(
        "--spot",
        "-s",
        type=str,
        default=None,
        help="Attraction name, destination, or URL",
    )
    parser.add_argument(
        "--batch",
        "-b",
        type=str,
        default=None,
        help="Batch spot list file: Excel (.xlsx, .xls) or CSV (.csv)",
    )
    parser.add_argument(
        "--pages",
        "-p",
        type=int,
        default=3,
        help="Maximum pages per attraction (10 reviews per page, default: 3)",
    )
    parser.add_argument(
        "--format",
        "-f",
        type=str,
        default="auto",
        choices=["auto", "excel", "sqlite", "csv", "json", "all"],
        help="Export format: auto (by suffix) | excel | sqlite | csv | json | all",
    )
    parser.add_argument(
        "--output",
        "-o",
        type=str,
        default="ctrip_reviews.xlsx",
        help=(
            "Output file name or path. Relative paths are resolved under output/ "
            "(default: 'ctrip_reviews.xlsx' -> output/ctrip_reviews.xlsx)"
        ),
    )
    parser.add_argument(
        "--no-cdp",
        action="store_true",
        help="Disable CDP mode (CDP is enabled by default and is the most reliable)",
    )
    parser.add_argument(
        "--cdp-port",
        type=int,
        default=9222,
        help="CDP remote debugging port (default: 9222)",
    )
    parser.add_argument(
        "--no-headless",
        action="store_true",
        help="Show the browser window (default: headless)",
    )
    parser.add_argument(
        "--log-file",
        type=str,
        default=None,
        help="Also write the run log to this file",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Enable DEBUG-level logging",
    )
    parser.add_argument(
        "--require-login",
        action="store_true",
        help="Require a signed-in Ctrip session; abort instead of crawling anonymously",
    )
    parser.add_argument(
        "--login-wait",
        type=int,
        default=0,
        help="Seconds to wait for you to sign in (0 = do not wait, crawl anonymously)",
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
            logging.getLogger(__name__).error("Failed to read the batch file: %s", exc)
            raise SystemExit(1) from exc
    elif spot_target:
        spots = [spot_target]
    else:
        # 未输入任何参数时给出引导提示
        logging.getLogger(__name__).info(
            "No attraction given; using the default example: '故宫博物院'"
        )
        spots = ["故宫博物院"]

    # 初始化采集器 (CDP 默认开启)
    crawler = CtripSightCrawler(
        use_cdp=not args.no_cdp,
        cdp_port=args.cdp_port,
        headless=not args.no_headless,
        require_login=args.require_login,
        login_wait=args.login_wait,
    )

    # 运行采集
    try:
        asyncio.run(
            crawler.run(
                spots=spots,
                max_pages=args.pages,
                output_file=args.output,
                format_type=args.format,
            )
        )
    except RuntimeError as exc:
        logging.getLogger(__name__).error("%s", exc)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
