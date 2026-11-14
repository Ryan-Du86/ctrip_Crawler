"""
ctrip_crawler 模块导出
"""

from .browser_manager import ensure_cdp_browser
from .crawler import CtripSightCrawler
from .exporter import DataExporter
from .logging_setup import configure_console_encoding, setup_logging
from .paths import EXAMPLES_DIR, OUTPUT_DIR, PROJECT_ROOT

__all__ = [
    "CtripSightCrawler",
    "DataExporter",
    "ensure_cdp_browser",
    "setup_logging",
    "configure_console_encoding",
    "PROJECT_ROOT",
    "OUTPUT_DIR",
    "EXAMPLES_DIR",
]
