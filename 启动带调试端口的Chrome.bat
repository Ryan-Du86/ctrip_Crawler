@echo off
chcp 65001 >nul
echo ========================================================
echo 正在启动支持 CDP 接管的 Chrome 浏览器...
echo (启动后您可以在此浏览器中自由浏览、登录携程，爬虫将直接调用此窗口)
echo ========================================================

start "" chrome.exe --remote-debugging-port=9222 --user-data-dir="%LOCALAPPDATA%\Google\Chrome_CDP_Profile"
exit
