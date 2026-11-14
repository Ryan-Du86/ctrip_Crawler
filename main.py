"""
携程（Ctrip）景点用户点评智能采集工具 - 运行入口
支持两种运行方式：
1. 编辑器模式：直接在下方【编辑器参数配置区】修改变量，点击编辑器右上角【运行】按钮即可。
2. 命令行模式：通过终端运行并传入参数，例如 `uv run main.py "故宫" -p 5`
"""

import asyncio
import sys

from ctrip_crawler.cli import main as cli_main
from ctrip_crawler.crawler import CtripSightCrawler
from ctrip_crawler.logging_setup import setup_logging

# ==============================================================================
# 🛠️ 编辑器参数配置区 (在 VS Code / PyCharm 中直接修改下方参数，点击运行即可)
# ==============================================================================

# 【1. 采集目标配置】
# 模式 A：单个景点采集（输入景点名称、目的地或携程景点完整 URL 均可）
# 示例: "故宫"、"武汉东湖"、"九寨沟"、"西湖"、"https://you.ctrip.com/sight/1/229.html"
SPOT_TARGET = "故宫"

# 模式 B：批量从表格导入采集（若启用请填写表格路径，不启用请保持 None）
# 支持 .xlsx, .xls, .csv 格式。表格中只要包含一列景点名称即可自动识别。
# 示例: "景点名单_示例.xlsx" 或 None
BATCH_FILE = None


# 【2. 采集深度配置】
# 每个景点的最大翻页采集数量（每页 10 条真实点评，设为 3 即采集前 30 条）
MAX_PAGES = 3


# 【3. 数据导出配置】
# 输出文件名或基础路径。
# 相对路径统一落在项目的 output/ 目录下，例如上面这个会写到：
#   output/ctrip_reviews.xlsx
# 若想写到别处，填绝对路径即可。
OUTPUT_FILE = "ctrip_reviews.xlsx"

# 导出格式，支持以下选项：
# - "auto"   : 根据 OUTPUT_FILE 的后缀名自动判断 (.xlsx -> Excel, .csv -> CSV, .db -> SQLite, .json -> JSON)
# - "excel"  : 导出为 Excel (.xlsx / .xls) 表格
# - "csv"    : 导出为 UTF-8 BOM CSV 文件
# - "sqlite" : 导出为 SQLite 关系型数据库文件 (.db)
# - "json"   : 导出为 JSON 数组文件 (.json)
# - "all"    : 一键同时生成 Excel、SQLite、CSV、JSON 全部 4 种文件！
EXPORT_FORMAT = "auto"


# 【4. 浏览器与反爬配置】
# 是否开启 CDP 协议接管模式（默认 True，防封能力最强）
# 开启后，爬虫会【直接接管当前已经打开的 Chrome/Edge 浏览器标签页】，不重新打开新窗口，爬取完毕也不关闭浏览器！
USE_CDP = True

# 是否使用无头模式（接管外部已打开的真实浏览器时，此项自动遵循外部窗口的显示状态）
HEADLESS = False

# CDP 远程调试端口（默认 9222，若被占用系统会自动顺延到空闲端口）
CDP_PORT = 9222


# 【5. 登录态配置】
# 爬虫复用你浏览器里已有的会话。只要在同一个浏览器窗口登录过携程，
# 就能自动识别登录态，不会再以匿名身份采集。
# The crawler reuses the session already present in your browser. Sign in to Ctrip
# in that same browser window and the signed-in state is detected automatically.

# 是否必须已登录才能继续采集（True = 未登录直接报错退出，不再匿名采集）
REQUIRE_LOGIN = False

# 未登录时，等待你在浏览器窗口完成登录的秒数（0 = 不等待，直接匿名采集）
# 需要扫码 / 短信验证码时，把它设成 180 之类的值
LOGIN_WAIT = 0

# ==============================================================================


def run_from_editor():
    """在编辑器中直接点击运行时执行的逻辑"""
    logger = setup_logging()
    logger.info("Editor mode detected; loading the configuration block above...")

    if BATCH_FILE:
        logger.info("Batch mode: loading the spot list from %s", BATCH_FILE)
        spots = CtripSightCrawler.load_spots_from_file(BATCH_FILE)
    elif SPOT_TARGET:
        logger.info("Single attraction mode: target = %s", SPOT_TARGET)
        spots = [SPOT_TARGET]
    else:
        logger.info("No target configured; using the default attraction: Forbidden City")
        spots = ["故宫"]

    crawler = CtripSightCrawler(
        use_cdp=USE_CDP,
        cdp_port=CDP_PORT,
        headless=HEADLESS,
        require_login=REQUIRE_LOGIN,
        login_wait=LOGIN_WAIT,
    )

    asyncio.run(
        crawler.run(
            spots=spots,
            max_pages=MAX_PAGES,
            output_file=OUTPUT_FILE,
            format_type=EXPORT_FORMAT,
        )
    )


if __name__ == "__main__":
    # 如果命令行传入了额外参数（如通过终端运行带有 -p, -o 等参数），则走 CLI 解析器
    # 如果没有额外参数（例如直接在 VS Code / PyCharm 中点击 Run 按钮），则直接使用上方的配置变量
    if len(sys.argv) > 1:
        cli_main()
    else:
        run_from_editor()
