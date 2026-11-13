# -*- coding: utf-8 -*-
"""
浏览器管理模块：支持 CDP 远程调试端口自动探测与启动
"""

import os
import socket
import subprocess
import time
import urllib.request
from typing import Optional, Tuple


def find_browser_executable() -> Optional[str]:
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
        req = urllib.request.urlopen(f"http://127.0.0.1:{port}/json/version", timeout=1)
        return req.getcode() == 200
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


def ensure_cdp_browser(
    port: int = 9222,
    user_data_dir: Optional[str] = None,
    headless: bool = True,
) -> Tuple[str, Optional[subprocess.Popen]]:
    """
    确保 CDP 调试浏览器正在运行。
    1. 优先复用已有 CDP 端口服务；
    2. 若未就绪，自动寻找空闲端口拉起本地 Chrome/Edge 浏览器并暴露调试。
    :return: (cdp_endpoint, process_handle)
    """
    candidate_ports = [port, 9333, 9444, 9555, 9666]

    # 1. 优先检测是否已有正常工作的 CDP 服务
    for p in candidate_ports:
        if is_cdp_active(p):
            print(f"[*] 检测到 CDP 调试浏览器已就绪: http://127.0.0.1:{p}")
            return f"http://127.0.0.1:{p}", None

    # 2. 挑选一个未被占用的空闲端口
    bind_port = None
    for p in candidate_ports:
        if is_port_available_for_bind(p):
            bind_port = p
            break

    if not bind_port:
        raise RuntimeError("未找到可用的本地网络端口启动 CDP 服务。")

    browser_path = find_browser_executable()
    if not browser_path:
        raise RuntimeError("未在系统中找到 Chrome 或 Edge 浏览器安装路径。")

    if not user_data_dir:
        user_data_dir = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "browser_profile"
        )
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

    print(f"[*] 正在自动拉起本地浏览器 CDP 服务: {os.path.basename(browser_path)} (调试端口: {bind_port})")
    proc = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    # 轮询等待端口就绪
    for _ in range(20):
        time.sleep(0.5)
        if is_cdp_active(bind_port):
            print(f"[√] 本地 CDP 浏览器就绪: http://127.0.0.1:{bind_port}")
            return f"http://127.0.0.1:{bind_port}", proc

    raise RuntimeError(f"CDP 浏览器启动超时（端口 {bind_port} 未就绪），请检查是否有防火墙/杀毒拦截。")
