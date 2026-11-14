"""浏览器管理与日志配置测试"""

import os
import sys

from ctrip_crawler.browser_manager import (
    candidate_ports,
    default_user_data_dir,
    find_browser_executable,
)
from ctrip_crawler.logging_setup import configure_console_encoding, setup_logging


def test_candidate_ports_prefers_requested():
    ports = candidate_ports(9222)
    assert ports[0] == 9222
    assert 9333 in ports
    assert len(ports) == len(set(ports)), "候选端口不应重复"


def test_candidate_ports_keeps_custom_first():
    ports = candidate_ports(7777)
    assert ports[0] == 7777


def test_default_user_data_dir_uses_localappdata():
    """自动拉起浏览器时应复用已登录的日常 profile，而不是仓库内的空目录。"""
    chrome = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
    edge = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"

    chrome_dir = default_user_data_dir(chrome)
    edge_dir = default_user_data_dir(edge)

    assert chrome_dir.endswith(os.path.join("Google", "Chrome_CDP_Profile"))
    assert edge_dir.endswith(os.path.join("Microsoft", "Edge_CDP_Profile"))

    # 不能落到仓库内的 browser_profile（那是个全新空 profile，会触发登录墙）
    assert "browser_profile" not in chrome_dir
    assert "browser_profile" not in edge_dir


def test_find_browser_executable_returns_str_or_none():
    result = find_browser_executable()
    assert result is None or result.endswith(".exe")


def test_configure_console_encoding_supports_chinese():
    """在 cp1252 控制台下打印中文不应抛 UnicodeEncodeError。"""
    original = sys.stdout
    try:
        sys.stdout = open(os.devnull, "w", encoding="cp1252", errors="strict")
        configure_console_encoding()
        print("[√] 导出完成！共保存 3 条点评记录")
    finally:
        sys.stdout.close()
        sys.stdout = original


def test_setup_logging_is_idempotent():
    """重复调用不应让日志重复输出。"""
    import logging

    setup_logging()
    first = len(logging.getLogger().handlers)
    setup_logging()
    second = len(logging.getLogger().handlers)
    assert first == second


def test_setup_logging_writes_file(tmp_path):
    import logging

    log_file = tmp_path / "run.log"
    setup_logging(log_file=str(log_file))
    logging.getLogger("test").info("中文日志内容")
    logging.getLogger().handlers[-1].flush()
    assert "中文日志内容" in log_file.read_text(encoding="utf-8")
