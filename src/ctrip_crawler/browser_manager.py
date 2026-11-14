"""
浏览器管理模块：支持 CDP 远程调试端口自动探测与启动
"""

import logging
import os
import socket
import subprocess
import tempfile
import time
import urllib.request
from pathlib import Path

logger = logging.getLogger(__name__)

# 备用调试端口（9222 被占用时依次尝试）
FALLBACK_PORTS = [9333, 9444, 9555, 9666]
# 端口探测超时（秒）
PROBE_TIMEOUT = 1


def find_browser_executable() -> str | None:
    """探测 Windows 系统本地 Chrome 或 Edge 路径"""
    candidates = [
        # Google Chrome
        os.path.expandvars(r"%ProgramFiles%\Google\Chrome\Application\chrome.exe"),
        os.path.expandvars(r"%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe"),
        os.path.expandvars(r"%LocalAppData%\Google\Chrome\Application\chrome.exe"),
        # Microsoft Edge
        os.path.expandvars(r"%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe"),
        os.path.expandvars(r"%ProgramFiles%\Microsoft\Edge\Application\msedge.exe"),
    ]
    for path in candidates:
        if os.path.exists(path):
            return path
    return None


def is_cdp_active(port: int) -> bool:
    """检查指定端口是否开放了正常的 CDP 调试服务"""
    try:
        with urllib.request.urlopen(
            f"http://127.0.0.1:{port}/json/version", timeout=PROBE_TIMEOUT
        ) as resp:
            return resp.getcode() == 200
    except Exception:
        return False


def is_port_available_for_bind(port: int) -> bool:
    """检查指定端口是否空闲可用"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind(("127.0.0.1", port))
            return True
        except OSError:
            return False


def default_user_data_dir(browser_path: str) -> str:
    """
    推断该浏览器的默认用户数据目录。

    关键点：必须复用用户日常使用、已经登录携程的那个 profile，
    否则自动拉起的浏览器是全新的未登录实例，会直接撞上登录墙。
    与 README 中说明的手动启动参数保持一致。
    """
    browser_name = Path(browser_path).stem.lower()
    local_appdata = os.environ.get("LOCALAPPDATA", "")

    if "edge" in browser_name:
        profile = os.path.join(local_appdata, "Microsoft", "Edge_CDP_Profile")
    else:
        profile = os.path.join(local_appdata, "Google", "Chrome_CDP_Profile")

    if profile:
        return profile
    # 极端情况兜底：系统变量缺失时退回临时目录下的独立 profile
    return os.path.join(tempfile.gettempdir(), "ctrip_crawler_cdp_profile")


def candidate_ports(port: int) -> list[int]:
    """构造候选端口列表：首选指定端口，再依次尝试备用端口。"""
    ports = [port]
    for p in FALLBACK_PORTS:
        if p not in ports:
            ports.append(p)
    return ports


def ensure_cdp_browser(
    port: int = 9222,
    user_data_dir: str | None = None,
    headless: bool = True,
) -> tuple[str, subprocess.Popen | None]:
    """
    确保 CDP 调试浏览器正在运行。

    1. 优先复用已有 CDP 端口服务；
    2. 若未就绪，自动寻找空闲端口拉起本地 Chrome/Edge 浏览器并暴露调试。

    :param port: 首选调试端口
    :param user_data_dir: 浏览器用户数据目录，为 None 时自动推断为已登录的日常 profile
    :param headless: 是否以无头模式启动
    :return: (cdp_endpoint, process_handle)
    """
    ports = candidate_ports(port)

    # 1. 优先检测是否已有正常工作的 CDP 服务
    for p in ports:
        if is_cdp_active(p):
            logger.info("CDP browser already running: http://127.0.0.1:%d", p)
            return f"http://127.0.0.1:{p}", None

    # 2. 挑选一个未被占用的空闲端口
    bind_port = None
    for p in ports:
        if is_port_available_for_bind(p):
            bind_port = p
            break

    if not bind_port:
        raise RuntimeError("No free local port available for the CDP service.")

    browser_path = find_browser_executable()
    if not browser_path:
        raise RuntimeError("Could not find a Chrome or Edge installation on this system.")

    if not user_data_dir:
        user_data_dir = default_user_data_dir(browser_path)
    os.makedirs(user_data_dir, exist_ok=True)

    args = [
        browser_path,
        f"--remote-debugging-port={bind_port}",
        f"--user-data-dir={user_data_dir}",
        "--disable-blink-features=AutomationControlled",
        "--no-first-run",
        "--no-default-browser-check",
    ]
    if headless:
        args.append("--headless=new")

    logger.info(
        "Launching a local browser with CDP: %s (port: %d, profile: %s)",
        os.path.basename(browser_path),
        bind_port,
        user_data_dir,
    )
    proc = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    # 轮询等待端口就绪
    for _ in range(20):
        time.sleep(0.5)
        if is_cdp_active(bind_port):
            logger.info("Local CDP browser ready: http://127.0.0.1:%d", bind_port)
            return f"http://127.0.0.1:{bind_port}", proc

    # 启动失败时不要留下孤儿浏览器进程
    try:
        proc.terminate()
    except Exception:
        pass
    raise RuntimeError(
        f"Timed out waiting for the CDP browser (port {bind_port} never became ready). "
        "Check for a firewall or antivirus blocker."
    )
