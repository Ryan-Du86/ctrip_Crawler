"""
日志与控制台编码配置。

Windows 控制台默认编码可能是 cp936 / cp1252，直接 print 中文与「√」等符号
会抛 UnicodeEncodeError。这里在启动时把标准输出/错误流统一切到 UTF-8
（errors="replace" 兜底，保证任何情况下都不会因为编码问题中断爬虫）。
"""

import logging
import sys

LOG_FORMAT = "%(asctime)s [%(levelname)s] %(message)s"
DATE_FORMAT = "%H:%M:%S"


def configure_console_encoding() -> None:
    """将 stdout/stderr 切换到 UTF-8，避免中文输出在旧版控制台下崩溃。"""
    for stream_name in ("stdout", "stderr"):
        stream = getattr(sys, stream_name, None)
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (ValueError, OSError):
            # 流已被关闭或不支持重配置，忽略即可
            pass


def setup_logging(level: int = logging.INFO, log_file: str | None = None) -> logging.Logger:
    """
    初始化根日志器。

    :param level: 日志级别，默认 INFO
    :param log_file: 可选的日志文件路径，指定后同时写入文件
    :return: 根日志器
    """
    configure_console_encoding()

    root = logging.getLogger()
    root.setLevel(level)

    # 重复调用时先清空旧 handler，避免日志重复输出
    for handler in list(root.handlers):
        root.removeHandler(handler)
        handler.close()

    formatter = logging.Formatter(LOG_FORMAT, datefmt=DATE_FORMAT)

    console = logging.StreamHandler(stream=sys.stdout)
    console.setFormatter(formatter)
    root.addHandler(console)

    if log_file:
        file_handler = logging.FileHandler(log_file, encoding="utf-8")
        file_handler.setFormatter(logging.Formatter(LOG_FORMAT))
        root.addHandler(file_handler)

    # 第三方库日志降噪
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("asyncio").setLevel(logging.WARNING)

    return root
