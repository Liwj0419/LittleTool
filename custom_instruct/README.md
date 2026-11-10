# Custom Instruct

本工具在自己的 `config.json` 声明 `appName` 和 `tool.mode="resident"`，由 Manager 自动发现。所有命令在本工具目录运行。

`--show` 复用已有实例；`--hidden` 按工具形态启动；`--reload` 重读运行中实例的配置；`--quit` 独立退出；`--selftest` 无界面检查后退出。两个启动脚本原样透传参数，运行始终使用自己的 `.venv`。

按名字唤起指令的小启动器：`Ctrl+Alt+,` 叫出输入框，输入名字回车就跑。
常驻后台，不占任务栏；本工具不创建托盘。

底层：**pywebview**（窗口，WebView2 渲染）+ **pynput**（全局热键）。
不编译、不打包，`.venv` 里直接跑源码。

## 文件

| 文件 | 作用 |
| --- | --- |
| `custom_instruct_start.bat` | 双击启动（不留黑框，bat 自己立刻退出） |
| `run.sh` | 命令行启动，能带参数，方便调试 |
| `app.py` | 入口 |
| `config.json` | 全部配置：窗口尺寸、快捷键、行为开关、指令表 |
| `instruct/` | 源码（配置、解析、窗口、热键、执行） |
| `assets/index.html` | 界面（单文件，不引框架） |
| `assets/icon.ico` | 窗口图标 |
| `requirements.txt` | 运行依赖：pywebview / pynput |
| `.venv/` | 解释器环境，首次运行时自动创建 |

## 启动

```bat
custom_instruct_start.bat                 :: 双击也行
custom_instruct_start.bat --show          :: 启动时顺手把启动器亮出来
custom_instruct_start.bat --devtools      :: 带开发者工具
custom_instruct_start.bat --selftest      :: 无界面自检，写 selftest.log 并返回退出码
```

```bash
./run.sh                  # 同上，命令行用
./run.sh -c               # 用 python.exe，能看到控制台输出和报错
```

第一次运行没有 `.venv` 时，脚本会自己建环境、装 `requirements.txt`，不用手动敲命令。

无参数启动显示窗口；`--hidden` 静默待命。按 `Ctrl+Alt+,` 唤出，关闭窗口或按 Esc 只隐藏。

## 用法

1. `Ctrl+Alt+,` 全局唤出启动器。
2. 输入名字，列表实时过滤。
3. `Enter` 执行选中的那条，`Esc` 收起。
4. 点到别的地方也会自动收起。

| 按键 | 动作 |
| --- | --- |
| `Ctrl+Alt+,` | 全局唤出（在 `config.json` 里改） |
| `↑` / `↓` | 上下选，最后一行再按 `↓` 回到第一行 |
| `PageUp` / `PageDown` | 一次翻 5 条 |
| `Enter` | 执行 |
| `Esc` | 收起 |
| `F5` | 重新读取 `config.json` |
| `Ctrl+O` | 打开 `config.json` |

也可以直接用鼠标点某一行（双击执行）。

## 指令表

指令写在 `config.json` 的 `commands` 数组里，一条一个对象。**改完不用重启**，每次唤出都会重读。

```json
"commands": [
  { "name": "项目",        "command": "open",       "path": "C:/示例/项目" },
  { "name": "提交项目", "command": "svn commit", "path": "C:/示例/项目" }
]
```

| 字段 | 说明 |
| --- | --- |
| `name` | 输入框里打的名字，必填 |
| `command` | `open` / `svn commit` / `svn update`，可省略（等于 `open`） |
| `path` | 文件夹或文件路径，支持 `%USERPROFILE%` 和 `~` |

| 指令 | 做什么 |
| --- | --- |
| `open` | 文件夹用资源管理器打开，文件用默认程序打开 |
| `svn commit` | 打开这个文件夹的 TortoiseSVN 提交窗口 |
| `svn update` | 打开这个文件夹的 TortoiseSVN 更新窗口 |

- 路径可以用正斜杠，例如 `"C:/示例/项目"`。如果使用反斜杠，在配置里写成两个，例如 `"C:\\示例\\项目"`。
- 同一个名字可以配多条指令，列表里分开显示。打 `项目` 列出所有相关的，打 `svn commit 提交项目` 可以直接命中提交指令。
- 相对路径按 `config.json` 所在目录算。
- 同一条指令写了两遍会以最后一条为准，并在状态栏提示有几处配置问题。

### 窗口大小

只认 `config.json` 的 `window`：

```json
"window": { "width": 400, "height": 380 }
```

实际显示尺寸有下限兜底，写再小也不会缩到没法用。

## config.json

带注释的 JSON（`//` 可以随便写）。

```json
{
  "window": { "width": 400, "height": 380 },
  "theme": "auto",
  "shortcuts": {
    "global":    "Ctrl+Alt+,",
    "hide":      "Esc",
    "run":       "Enter",
    "next":      "Down",
    "prev":      "Up",
    "page_next": "PageDown",
    "page_prev": "PageUp",
    "reload":    "F5",
    "edit":      "Ctrl+O"
  },
  "behavior": {
    "hide_on_blur":  true,
    "always_on_top": true,
    "beep_on_miss":  true
  },
  "commands": [
    { "name": "项目", "command": "open", "path": "C:/示例/项目" }
  ]
}
```

- `theme`：`auto` 跟随系统，也可以写 `dark` / `light`。
- 快捷键写成 `""` 就等于停用这个键；删掉整行则用内置默认值。
- 全局热键支持 `Ctrl` / `Alt` / `Shift` / `Win`，逗号这类符号直接写符号本身。
- 改完配置执行 `./run.sh --reload`，或者重开程序。

## 后台与退出

本工具不创建托盘；`Esc`、关闭按钮和 `Alt+F4` 只收起。`./run.sh --quit` 正常退出，`./run.sh --reload` 重读配置，不依赖 Manager。

## 开机启动

由用户在 Manager 中按需勾选，不自行创建系统启动项。

## 自检

`./run.sh --selftest` 无界面检查配置、指令解析、相对路径和精确匹配；不执行指令，结果写入 `selftest.log`，失败返回非 0。

## 日志

`%APPDATA%\CustomInstruct\app.log`，记录每次唤出、执行了哪条指令、出了什么错。

## 常见问题

| 现象 | 处理 |
| --- | --- |
| 窗口一片空白 / 启动即退 | 缺 WebView2 运行时（Win11 自带，Win10 装了 Edge 就有） |
| 热键没反应 | 先看 `app.log` 里有没有 `[hotkey] 已注册`：没有就是被别的软件占了，改 `config.json` 的 `shortcuts.global` 后重新加载；有注册记录却还是不响应，说明进程卡住了，重启本软件 |
| svn 指令报「没找到 TortoiseProc.exe」 | TortoiseSVN 的 `bin` 目录加进 PATH，或确认装在 `C:\Program Files\TortoiseSVN` |
| 改了指令没生效 | 每次唤出都会重读；如果窗口一直开着，按 `F5` |
| 想看到报错 | `./run.sh -c` 用带控制台的解释器跑 |
