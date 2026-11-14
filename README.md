# Ctrip Smart Review Crawler (携程景点点评智能采集工具)

专为携程景点用户真实点评采集打造的现代化爬虫工具，基于 **Python + CDP / Playwright**，使用 **`uv`** 进行环境与依赖管理。

支持两种使用习惯：
1. **编辑器可视化调参**：在 VS Code / PyCharm 等编辑器中直接修改变量，点击右上角【运行】按钮即可。
2. **终端命令行驱动**：在终端通过 `uv run main.py ...` 传递各种参数。

---

## 🛡️ CDP 模式核心优势：直接接管当前已打开的浏览器（不重开、不关闭）

这是防风控效果最好的模式：**直接调用你当前已经在看的浏览器界面**，无需重新开新浏览器，爬取完成后也**绝不会关闭你的浏览器**！

### 如何使用：
1. **启动浏览器（只需启动一次）**：
   双击项目目录下的脚本启动你的日常浏览器：
   - 使用 Chrome：双击运行 [启动带调试端口的Chrome.bat](file:///D:/worksations/ctrip_Crawler/启动带调试端口的Chrome.bat)
   - 使用 Edge：双击运行 [启动带调试端口的Edge.bat](file:///D:/worksations/ctrip_Crawler/启动带调试端口的Edge.bat)
   *(或者给桌面的 Chrome/Edge 快捷方式属性目标加上 `--remote-debugging-port=9222`，以后平时上网都用它即可)*

2. **在浏览器中随意浏览 / 登录携程**：
   在打开的窗口里，你可以登录携程、搜索景点，或者直接停留在某个景点的点评页面上。

3. **运行爬虫**：
   - 爬虫启动后会**直接识别并接管你当前屏幕上的那个标签页**；
   - 如果你当前正停留在黄鹤楼页面，爬虫连搜索都免了，直接在当前页面开始向下滚动翻页；
   - 采集完成导出 Excel 后，**当前浏览器窗口依然保留**，方便你随时在界面上核对数据！

---

## 💻 方式一：在编辑器中调参并直接运行（最简单）

无需在终端输入任何长命令，直接打开项目根目录下的 **[`main.py`](file:///D:/worksations/ctrip_Crawler/main.py)**，在最上方的 **【🛠️ 编辑器参数配置区】** 修改变量即可：

```python
# ==============================================================================
# 🛠️ 编辑器参数配置区 (在 VS Code / PyCharm 中直接修改下方参数，点击运行即可)
# ==============================================================================

# 【1. 采集目标配置】
SPOT_TARGET = "故宫"  # 支持景点名、目的地或完整 URL

# 【2. 采集深度配置】
MAX_PAGES = 3  # 每个景点的最大翻页采集数量（每页 10 条）

# 【3. 数据导出配置】
OUTPUT_FILE = "携程点评采集结果.xlsx"
EXPORT_FORMAT = "auto"  # "auto" | "excel" | "csv" | "sqlite" | "json" | "all"

# 【4. 浏览器与反爬配置】
USE_CDP = True  # 默认开启 CDP 接管
HEADLESS = False  # 接管外部浏览器时设为 False 可看实时翻页
CDP_PORT = 9222
# ==============================================================================
```

> **运行方法**：修改完上述变量保存后，在 VS Code / PyCharm 中直接点击右上角的绿色三角 **【运行 / Run】** 按钮即可！

---

## ⚡ 方式二：终端命令行驱动（通过 `uv run`）

如果您习惯使用终端或者部署在自动化脚本中：

```powershell
cd D:\worksations\ctrip_Crawler
```

### 1. 单个景点智能采集（输入名称即可）
```powershell
# 采集黄鹤楼 20 页（200 条），导出为 Excel
uv run main.py "黄鹤楼" --pages 20 -o 黄鹤楼200条.xlsx

# 采集武汉东湖绿道，直接存入 SQLite 数据库
uv run main.py "东湖绿道" --pages 5 -o 东湖点评.db
```

### 2. 从 Excel 表格批量导入批量爬取
项目目录下已附带模板：[景点名单_示例.xlsx](file:///D:/worksations/ctrip_Crawler/%E6%99%AF%E7%82%B9%E5%90%8D%E5%8D%95_%E7%A4%BA%E4%BE%8B.xlsx)
```powershell
# 批量采集 Excel 中的全部景点，每景抓 2 页
uv run main.py --batch 景点名单_示例.xlsx --pages 2 -o 批量结果.xlsx
```

### 3. 一键同步导出所有 4 种数据格式 (Excel + SQLite + CSV + JSON)
```powershell
uv run main.py "九寨沟" --pages 2 --format all -o 九寨沟全数据
```

---

## 📋 命令行参数速查表

| 参数 | 简写 | 默认值 | 作用说明 |
| :--- | :--- | :--- | :--- |
| `spot_arg` | - | `故宫` | 景点名称、目的地或直接 URL (位置参数) |
| `--spot` | `-s` | `None` | 待采集的景点名称/URL (选项参数) |
| `--batch` | `-b` | `None` | 批量导入文件路径，支持 `.xlsx`, `.xls`, `.csv` |
| `--pages` | `-p` | `3` | 每个景点的翻页采集数量（每页 10 条） |
| `--format`| `-f` | `auto` | 导出格式：`auto` (由后缀决定) \| `excel` \| `sqlite` \| `csv` \| `json` \| `all` |
| `--output`| `-o` | `携程点评采集结果.xlsx`| 输出文件名或基础路径 |
| `--no-cdp`| - | `False` | 禁用 CDP 模式（默认开启） |
| `--cdp-port`| - | `9222` | CDP 调试端口（若被占用会自动顺延选择空闲端口） |
| `--no-headless`| -| `False` | 显示真实浏览器界面窗口（默认后台无头运行） |
| `--log-file`| - | `None` | 将运行日志同时写入指定文件 |
| `--verbose`| `-v` | `False` | 输出 DEBUG 级别详细日志 |

> **编码提示**：程序启动时会自动把控制台输出切换到 UTF-8，因此在 cmd、PowerShell、
> git-bash 等各种终端下打印中文都不会再出现 `UnicodeEncodeError`。

---

## 🧪 开发与测试

```bash
# 安装开发依赖（pytest / ruff / mypy）
uv sync --group dev

# 运行测试
uv run pytest -q

# 代码检查与格式化
uv run ruff check .
uv run ruff format .

# 类型检查
uv run mypy
```

---

## 🛠️ 代码结构说明

```
src/ctrip_crawler/
├── crawler.py         # 采集主引擎：搜索解析、点评抽取、翻页逻辑
├── selectors.py       # 所有页面 CSS 选择器集中管理（携程改版只改这里）
├── exporter.py        # 多格式导出：CSV / Excel(.xlsx/.xls) / SQLite / JSON
├── browser_manager.py # CDP 端口探测与浏览器自动拉起
├── logging_setup.py   # 日志配置 + 控制台 UTF-8 编码修复
└── cli.py             # 命令行参数解析
```

**几个容易踩坑的点，已经处理好了：**

- **`.xls` 读写**：pandas 3.x 移除了 xlwt 写入引擎、xlrd 2.x 也已不支持写 `.xls`，
  项目内已用 xlwt 直写补齐，并用 calamine 读取 `.xls`。
- **SQLite 重复行**：默认按内容去重后追加，重复运行同一条命令不会产生重复记录
  （需要强制覆盖请调用 `DataExporter.export(..., overwrite=True)`）。
- **空表格**：读入空 sheet 会给出清晰的错误提示，而不是抛出裸 `IndexError`。
- **浏览器 profile**：自动拉起浏览器时复用 `%LOCALAPPDATA%` 下你日常已登录携程的
  profile，不会因为用了全新空 profile 而撞上登录墙。
- **去重**：点评按全文哈希去重，不会因为前 25 个字相同而误删不同的点评。

---

## 🗄️ 输出数据字段规范

无论是导出为 CSV、Excel、SQLite 还是 JSON，均包含以下统一的结构化字段：

| 字段名称 | 字段类型 | 示例内容 |
| :--- | :--- | :--- |
| **序号** | 整数 | `1` |
| **景区名称** | 字符串 | `故宫博物院, 北京` |
| **平台** | 字符串 | `携程` |
| **内容类型** | 字符串 | `用户点评` |
| **时间** | 字符串 (YYYY-MM-DD) | `2026-10-20` |
| **文本内容** | 文本 | `感谢纪导全程精彩的讲解，幽默风趣又娓娓道来...` |
| **字数** | 整数 | `100` |
| **评分** | 字符串 | `5分 超棒` |
| **IP属地** | 字符串 | `北京` |
| **用户名** | 字符串 | `匿名用户` |
