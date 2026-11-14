# Ctrip Smart Review Crawler | 携程景点点评智能采集工具

> 基于 **Python + CDP / Playwright** 的携程景点用户真实点评采集工具，使用 **`uv`** 管理依赖。
> A modern crawler for Ctrip (携程) attraction reviews, built on **Python + CDP / Playwright**.

---

## 📖 目录 | Table of Contents

- [项目结构 | Project Layout](#-项目结构--project-layout)
- [快速开始 | Quick Start](#-快速开始--quick-start)
- [🛡️ CDP 模式 | CDP Mode](#-cdp-模式--cdp-mode)
- [🔑 登录态 | Login State](#-登录态--login-state)
- [💻 方式一：编辑器调参 | Editor Mode](#-方式一编辑器调参--editor-mode)
- [⚡ 方式二：命令行 | CLI Mode](#-方式二命令行--cli-mode)
- [📋 参数速查表 | CLI Reference](#-参数速查表--cli-reference)
- [🗄️ 输出数据字段 | Output Schema](#-输出数据字段--output-schema)
- [🛠️ 代码结构 | Code Structure](#-代码结构--code-structure)
- [🧪 开发 | Development](#-开发--development)

---

## 📂 项目结构 | Project Layout

```
ctrip_Crawler/
├── README.md                  # 本文档 / This document
├── pyproject.toml             # 项目与依赖配置 / Project & dependency config
├── uv.lock                    # 依赖锁定 / Locked dependencies
├── main.py                    # 入口：编辑器调参 + CLI 分发 / Entry point
├── src/ctrip_crawler/        # 主包 / Main package
│   ├── crawler.py             # 采集主引擎 / Core crawler
│   ├── selectors.py           # CSS 选择器集中管理 / Centralized selectors
│   ├── exporter.py            # 多格式导出 / Multi-format export
│   ├── browser_manager.py     # CDP 探测与浏览器拉起 / CDP detection & launch
│   ├── login.py               # 登录态检测 / Login state detection
│   ├── logging_setup.py       # 日志与编码修复 / Logging & encoding
│   ├── paths.py               # 路径常量 / Path constants
│   ├── cli.py                 # 命令行解析 / CLI parsing
│   ├── __main__.py            # python -m 入口 / Module entry point
│   └── __init__.py
├── examples/
│   └── 景点名单_示例.xlsx      # 批量名单模板 / Batch spot list template
└── output/                    # 采集结果（已 gitignore）/ Crawl results (gitignored)
```

> **目录约定 | Directory convention**
> 采集结果统一落在 `output/`，相对路径即相对于该目录；
> Crawl results always land in `output/` — relative paths are resolved against it.

---

## 🚀 快速开始 | Quick Start

```bash
# 安装依赖 / Install dependencies
uv sync

# 单景点采集 / Crawl a single attraction
uv run main.py "故宫" --pages 3

# 也可以用包入口 / Or use the module entry point
uv run python -m ctrip_crawler "故宫" --pages 3

# 批量采集 / Batch crawl from a spreadsheet
uv run main.py --batch examples/景点名单_示例.xlsx --pages 2

# 一键导出全部 4 种格式 / Export all four formats at once
uv run main.py "九寨沟" --format all -o 九寨沟全数据
```

---

## 🛡️ CDP 模式 | CDP Mode

**核心思路**：直接接管你当前已经打开的浏览器，不新开窗口，采集完也不关闭。
**Core idea**: take over the browser you already have open — no new window, never closed at the end.

这是防风控效果最好的模式。
This is the mode with the strongest anti-ban behaviour.

### 步骤 | Steps

**1. 用调试端口启动浏览器（只需启动一次）**
**Start your browser with the debugging port (only once)**

在浏览器快捷方式的「目标」末尾加上 `--remote-debugging-port=9222`，例如：

```
"C:\Program Files\Google\Chrome\Application\chrome.exe" --remote-debugging-port=9222 --user-data-dir="%LOCALAPPDATA%\Google\Chrome_CDP_Profile"
```

Edge 对应参数：

```
"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe" --remote-debugging-port=9222 --user-data-dir="%LOCALAPPDATA%\Microsoft\Edge_CDP_Profile"
```

> ⚠️ **务必指定 `--user-data-dir`**
> 不指定的话浏览器会拒绝在默认 profile 上开启调试端口；
> 而复用带 CDP 的独立 profile，爬虫才能沿用你已登录携程的会话。
>
> **Always pass `--user-data-dir`** — Chrome refuses the debug port on the default profile,
> and a dedicated CDP profile keeps your existing Ctrip login session.

> 💡 端口被占用时，爬虫会自动顺延到 `9333 / 9444 / 9555 / 9666`。
> If the port is busy, the crawler automatically falls back to `9333 / 9444 / 9555 / 9666`.

**2. 在浏览器里自由浏览、登录携程**
**Browse and log in to Ctrip freely**

可以登录携程、搜索景点，或者直接停留在某个景点的点评页面上。
Log in, search, or just leave a tab open on the attraction's review page.

**3. 运行爬虫**
**Run the crawler**

- 爬虫会**直接识别并接管你当前屏幕上的那个标签页**；
  The crawler **detects and takes over your currently active tab**.
- 若你正停留在黄鹤楼页面，搜索都免了，直接在当前页面开始翻页；
  If you are already on the target page, the search step is skipped entirely.
- 采集完成导出后，**浏览器窗口依然保留**，方便随时核对数据；
  After the export, **your browser stays open** so you can spot-check the results.

---

## 🔑 登录态 | Login State

爬虫复用你浏览器里已有的会话，**登录过就能自动识别，不再匿名采集**。
The crawler reuses the session already in your browser — **sign in once and it is detected automatically**.

### 它是怎么判断的 | How detection works

| 信号 | Signal | 说明 |
| :--- | :--- | :--- |
| **页面头部** | DOM header | 页面右上角出现「登录 / 注册」按钮 = 未登录；登录后按钮消失、显示账号昵称 |
| **会话 Cookie** | Auth cookies | 携程登录后才会下发的 cookie（`_bfa`、`GUID`、`_pd` 等匿名访客也会拿到的已排除） |

> ⚠️ 匿名访问时，携程会把点评用户名显示成「匿名用户」。登录后才能拿到真实昵称。
> When browsing anonymously, Ctrip masks reviewer names as "匿名用户".
> Sign in to capture the real nicknames.

### 三种用法 | Three ways to use it

```bash
# 1) 默认：检测到就登录态采集，没登录就匿名采集
uv run main.py "故宫"

# 2) 要求登录：没登录直接报错退出，不做匿名采集
uv run main.py "故宫" --require-login

# 3) 等待登录：给你 180 秒在浏览器窗口里扫码 / 收验证码登录
uv run main.py "故宫" --login-wait 180
```

### 编辑器模式 | Editor mode

在 [`main.py`](main.py) 的 **【5. 登录态配置】** 区块：

```python
# 【5. 登录态配置】Login
REQUIRE_LOGIN = False  # True = 未登录直接退出
LOGIN_WAIT = 0  # 未登录时等待你登录的秒数（扫码场景设 180）
```

### 终端输出 | Terminal output

CDP 连接状态与登录态会以横幅形式打印：

```
[INFO] [...] CDP CONNECTING   | endpoint: http://127.0.0.1:9222
[INFO] [ OK ] CDP CONNECTED    | endpoint: http://127.0.0.1:9222 | tab: https://you.ctrip.com/
[INFO] Session: Logged in (existing session)
...
[INFO] [ ... ] CDP DISCONNECTED | tab: browser left open
```

---

## 💻 方式一：编辑器调参 | Editor Mode

打开项目根目录下的 [`main.py`](main.py)，在最上方的 **【🛠️ 编辑器参数配置区】** 修改变量即可。

Open [`main.py`](main.py) and edit the variables in the **【🛠️ 编辑器参数配置区】** block.

```python
# 【1. 采集目标配置】Target
SPOT_TARGET = "故宫"  # 景点名 / 目的地 / 完整 URL

# 【2. 采集深度配置】Depth
MAX_PAGES = 3  # 每个景点翻页数，每页 10 条

# 【3. 数据导出配置】Export
OUTPUT_FILE = "ctrip_reviews.xlsx"  # -> output/ctrip_reviews.xlsx
EXPORT_FORMAT = "auto"  # auto | excel | csv | sqlite | json | all

# 【4. 浏览器与反爬配置】Browser
USE_CDP = True  # 默认开启 CDP 接管
HEADLESS = False  # 接管外部浏览器时设为 False 可看实时翻页
CDP_PORT = 9222
```

> 保存后，在 VS Code / PyCharm 中点击右上角绿色三角 **【运行 / Run】** 即可。
> Save, then hit the green **Run** triangle in VS Code / PyCharm.

---

## ⚡ 方式二：命令行 | CLI Mode

```powershell
cd D:\worksations\ctrip_Crawler
```

### 1. 单个景点智能采集 | Single attraction

```powershell
# 采集黄鹤楼 20 页（200 条），导出为 Excel
uv run main.py "黄鹤楼" --pages 20 -o 黄鹤楼200条.xlsx

# 采集武汉东湖绿道，直接存入 SQLite 数据库
uv run main.py "东湖绿道" --pages 5 -o 东湖点评.db
```

### 2. 从表格批量导入 | Batch crawl from spreadsheet

模板见 [`examples/景点名单_示例.xlsx`](examples/景点名单_示例.xlsx)，只要含一列景点名称即可自动识别。

```powershell
uv run main.py --batch examples/景点名单_示例.xlsx --pages 2 -o 批量结果.xlsx
```

### 3. 一次导出全部格式 | Export every format

```powershell
uv run main.py "九寨沟" --pages 2 --format all -o 九寨沟全数据
```

---

## 📋 参数速查表 | CLI Reference

| 参数 | 简写 | 默认值 | 说明 | Description |
| :--- | :--- | :--- | :--- | :--- |
| `spot_arg` | - | `故宫` | 景点名称/URL（位置参数） | Attraction name or URL (positional) |
| `--spot` | `-s` | `None` | 景点名称/URL（选项参数） | Attraction name or URL (option) |
| `--batch` | `-b` | `None` | 批量名单文件 | Batch list file (`.xlsx`/`.xls`/`.csv`) |
| `--pages` | `-p` | `3` | 每景点翻页数（每页 10 条） | Pages per attraction (10 per page) |
| `--format` | `-f` | `auto` | 导出格式 | Export format: `auto`\|`excel`\|`sqlite`\|`csv`\|`json`\|`all` |
| `--output` | `-o` | `携程点评采集结果.xlsx` | 输出文件（相对路径落 `output/`） | Output file (relative paths go to `output/`) |
| `--no-cdp` | - | `False` | 禁用 CDP 模式 | Disable CDP mode |
| `--cdp-port` | - | `9222` | CDP 调试端口 | CDP debugging port |
| `--no-headless` | - | `False` | 显示浏览器窗口 | Show the browser window |
| `--log-file` | - | `None` | 同时写入日志文件 | Also write logs to a file |
| `--verbose` | `-v` | `False` | DEBUG 级日志 | DEBUG-level logging |
| `--require-login` | - | `False` | 要求已登录携程，否则退出 | Require a signed-in session; abort if not signed in |
| `--login-wait` | - | `0` | 等待你登录的秒数 | Seconds to wait for you to sign in |

> **编码提示 | Encoding note**
> 程序启动时会把控制台输出切到 UTF-8，因此在 cmd、PowerShell、git-bash 下打印中文都不会再报 `UnicodeEncodeError`。
> The console is switched to UTF-8 at startup, so Chinese output no longer raises `UnicodeEncodeError` on any terminal.

---

## 🗄️ 输出数据字段 | Output Schema

导出为 CSV、Excel、SQLite 还是 JSON，均包含以下统一字段：
All four formats share the same schema:

| 字段名称 | Field | 类型 | 示例 | Example |
| :--- | :--- | :--- | :--- | :--- |
| **序号** | index | 整数 | `1` | `1` |
| **景区名称** | attraction | 字符串 | `故宫博物院, 北京` | `故宫博物院, 北京` |
| **平台** | platform | 字符串 | `携程` | `携程` |
| **内容类型** | content_type | 字符串 | `用户点评` | `用户点评` |
| **时间** | date | 字符串 | `2026-10-20` | `2026-10-20` |
| **文本内容** | content | 文本 | `感谢纪导全程精彩的讲解…` | `感谢纪导全程精彩的讲解…` |
| **字数** | length | 整数 | `100` | `100` |
| **评分** | rating | 字符串 | `5分 超棒` | `5分 超棒` |
| **IP属地** | ip_location | 字符串 | `北京` | `北京` |
| **用户名** | username | 字符串 | `匿名用户` | `匿名用户` |

---

## 🛠️ 代码结构 | Code Structure

| 模块 | Module | 职责 | Responsibility |
| :--- | :--- | :--- | :--- |
| `crawler.py` | | 搜索解析、点评抽取、翻页 | Search resolution, review extraction, pagination |
| `selectors.py` | | 全部 CSS 选择器集中管理 | All CSS selectors in one place |
| `exporter.py` | | CSV / Excel / SQLite / JSON 导出 | Multi-format export |
| `browser_manager.py` | | CDP 端口探测与浏览器拉起 | CDP detection & browser launch |
| `login.py` | | 登录态检测与等待登录 | Login state detection & waiting for sign-in |
| `logging_setup.py` | | 日志配置 + 控制台编码修复 | Logging & console encoding fix |
| `paths.py` | | 路径常量 | Path constants |
| `cli.py` | | 命令行参数解析 | CLI parsing |

> 💡 **携程改版时只改 `selectors.py`** —— 页面类名集中在这里，不必满项目排查。
> When Ctrip redesigns the page, **only `selectors.py` needs editing**.

**几个容易踩坑的点，已经处理好了 | Gotchas already handled**

- **`.xls` 读写** — pandas 3.x 移除了 xlwt 写入引擎、xlrd 2.x 也不再支持写 `.xls`；
  项目内改用 xlwt 直写 BIFF8、calamine 读取。
  pandas 3.x dropped the xlwt writer and xlrd 2.x can no longer write `.xls`;
  the project writes BIFF8 directly via xlwt and reads via calamine.
- **SQLite 重复行** — 默认按内容去重后追加，重复运行同一命令不会产生重复记录。
  Rows are de-duplicated before appending, so re-running a command never duplicates data.
- **空表格** — 读入空 sheet 给出清晰错误提示，而不是抛出裸 `IndexError`。
  An empty sheet raises a clear error instead of a bare `IndexError`.
- **浏览器 profile** — 自动拉起浏览器时复用 `%LOCALAPPDATA%` 下已登录携程的 profile，
  不会因为全新空 profile 而撞上登录墙。
  The auto-launched browser reuses your logged-in profile, so you won't hit a login wall.
- **去重** — 点评按全文哈希去重，不会因前 25 字相同而误删不同的点评。
  Reviews are de-duplicated by a full-content hash, not a truncated prefix.

---

## 🧪 开发 | Development

```bash
# 安装开发依赖 / Install dev dependencies
uv sync --group dev

# 代码检查与格式化 / Lint and format
uv run ruff check .
uv run ruff format .

# 类型检查 / Type check
uv run mypy
```

---

## ⚠️ 使用须知 | Disclaimer

本工具仅供个人学习与研究使用。请遵守目标网站的 `robots.txt` 与服务条款，
控制采集频率，勿用于商业用途或大规模数据抓取。

For personal study and research only. Please respect the target site's `robots.txt`
and Terms of Service, keep the request rate reasonable, and do not use it for
commercial purposes or bulk harvesting.
