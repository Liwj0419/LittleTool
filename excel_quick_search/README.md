# Excel 快速搜索

本工具在自己的 `config.json` 声明 `appName` 和 `tool.mode="resident"`，由 Manager 自动发现。所有命令在本工具目录运行。

`--show` 复用已有实例；`--hidden` 按工具形态启动；`--reload` 重读运行中实例的配置；`--quit` 独立退出；`--selftest` 无界面检查后退出。两个启动脚本原样透传参数，运行始终使用自己的 `.venv`。

按 `Ctrl+Alt+2` 唤出窗口，在配置好的目录里按文件名找 Excel，按回车用系统默认软件打开。

- 窗口：**pywebview**（WebView2 渲染 `assets/index.html`）
- 全局热键：**pynput**
- 本工具不创建托盘，统一由 Manager 展示和管理
- 不编译、不打包，源码直接跑

---

## 运行

```bash
./run.sh                 # 或直接双击 excel_quick_search_start.bat
```

第一次运行会自动建 `.venv` 并装 `requirements.txt`，不用手动敲命令。
`run.sh` 额外支持：

| 参数 | 作用 |
| --- | --- |
| `--hidden` | 静默启动，后台待命，不创建托盘（配合开机自启） |
| `--devtools` | 打开 WebView2 开发者工具 |
| `--selftest` | 无界面自检：写 `selftest.log`，按结果返回退出码 |
| `-c` | 用 `python.exe` 跑，能看到控制台输出和报错 |

`excel_quick_search_start.bat` 和 `run.sh` 原样透传程序参数；`-c` 是命令行脚本的控制台选项。

---

## 怎么用

1. 打开 `config.json`，在 `sources` 里把 `"path": ""` 填成自己的表格目录，例如 `"path": "C:/表格"`。保存后重启工具。
2. 按 `Ctrl+Alt+2` 唤出，在搜索框输入文件名。配置了多个目录时，先选择一个。
3. `↑` `↓` 选择，`Enter` 打开，`Esc` 收起（进程继续在后台待命）。
4. `./run.sh --reload` 重读配置；`./run.sh --quit` 独立退出。

**只配一个数据源时不弹选择框**：`config.json` 的 `sources` 里只留一条时，唤起后直接扫描这一条，
光标直接落在搜索框；配置里有两条以上才出现选择框。

**收起即重置**：`Esc`、点「收起」、打开文件后自动隐藏，都会把搜索框和结果清空
（在窗口隐藏之后清，所以下次唤起是干干净净的，不会先闪一下上次的内容）。

匹配规则和旧版一致：文件名**开头**命中的排在前面，其次按命中位置越靠前越优先。

在 `config.json` 中把 `open.prefer_shower` 设为 `true`，可以优先使用同级的 Excel 数据查看器。查看器不可用时，使用系统默认软件。

---

## 配置

全部在 `config.json`（支持 `//` 注释），改完执行 `./run.sh --reload` 即可生效。

| 段 | 说明 |
| --- | --- |
| `window` | 宽高、主题（`dark` / `light` / `auto`）、是否置顶 |
| `shortcuts.global` | 全局唤起键，可以写字符串，也可以写数组挂多个 |
| `shortcuts.*` | 窗口内快捷键：收起 / 聚焦 / 打开 / 定位 / 复制路径 / 重新扫描 |
| `sources` | 数据源列表，`key` 是唤起后按的那个键，`path` 可以是目录也可以是单个 xlsx；只留一条时免选择 |
| `search` | 扩展名白名单、跳过前缀、最多收多少个文件、递归深度 |
| `open` | 是否优先交给 excel_shower、它在哪 |

快捷键写法：`Ctrl+Alt+2`、`Ctrl+Shift+C`、`F5`、`Esc`……符号键直接写字符（`. , ; / [ ] - = \``）。
`>` 会自动当成 `Shift+.` 处理，所以 `Ctrl+Alt+>` 和 `Ctrl+Alt+.` 是两个不同的组合，两个都能挂。

---

## 目录结构

```
app.py                 入口（解析 --hidden / --devtools / --selftest）
config.json            配置
requirements.txt       依赖
run.sh / excel_quick_search_start.bat     两个入口，行为一致
assets/index.html      整个界面（单文件，无框架、无构建）
assets/icon.ico        窗口图标
quicksearch/
  core.py              把窗口、热键、扫描串起来
  window.py            pywebview 窗口 + JS 桥
  hotkey.py            pynput 全局热键
  scan.py              目录扫描
  opener.py            打开 / 定位文件（含 excel_shower 联动）
  native.py            Win32：抢前台、居中、深色标题栏
  single.py            单实例
  selftest.py          --selftest 的实现
```

改界面只动 `assets/index.html`，重开程序就生效（页面是运行时从磁盘读的）。
日志在 `%APPDATA%\ExcelQuickSearch\app.log`。
