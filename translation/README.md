# 轻翻译

本工具在自己的 `config.json` 声明 `appName` 和 `tool.mode="resident"`，由 Manager 自动发现。所有命令在本工具目录运行。

`--show` 复用已有实例；`--hidden` 按工具形态启动；`--reload` 重读运行中实例的配置；`--quit` 独立退出；`--selftest` 无界面检查后退出。两个启动脚本原样透传参数，运行始终使用自己的 `.venv`。

一个常驻后台的桌面进程，按 **`Ctrl` + `Alt` + `2`** 随时唤出，只做中英互译。

## 怎么用

- **打开**：`Ctrl` + `Alt` + `2`，或双击 `translation_start.bat`（桌面上的「翻译」图标也是它）
- **收起**：`Esc`。窗口消失，进程继续在后台待命，下次唤出是瞬时的
- **退出**：`./run.sh --quit`，不依赖 Manager
- 翻译需要联网，输入的文字会发送给 MyMemory。免费用量有每日限制。 [MyMemory 用量说明](https://mymemory.translated.net/doc/usagelimits.php)

每次唤出都会把窗口拉到前台、接管键盘焦点，并清空上一次的输入，光标自动落在输入框。

## 快捷键

**所有快捷键都在 `config.json` 的 `shortcuts` 里，改完执行 `./run.sh --reload`即可生效。**

| 配置项 | 默认值 | 作用 |
| --- | --- | --- |
| `global` | `Ctrl+Alt+2` | 全局唤出窗口（主键盘和小键盘的数字都能用） |
| `hide` | `Esc` | 收起窗口 |
| `focus` | `Ctrl+F` | 光标跳到输入框 |
| `translate` | `Ctrl+Enter` | 立即翻译 |
| `clear` | `Ctrl+L` | 清空 |
| `copy` | `Ctrl+Shift+C` | 复制译文 |

**把某一行删掉、留空或者注释掉，就表示不启用这个快捷键**：

```jsonc
"shortcuts": {
  "global": "Ctrl+Alt+2",
  "hide":   "Esc",
  // "clear": "Ctrl+L",        ← 注释掉，Ctrl+L 就不再清空
  "copy":   ""                 ← 留空，同样不启用
}
```

改完重新加载配置后，菜单里和空状态上的快捷键提示也会跟着变（没启用的不再显示）。

## 文件

| 文件 | 说明 |
| --- | --- |
| `translation_start.bat` | **双击这个就能用**（不弹黑框，第一次会自动建 `.venv` 装依赖；只管启动，不碰快捷方式） |
| `run.sh` | 命令行启动脚本，跟 bat 一样，方便在 bash 里带参数跑 |
| `app.py` | 程序入口 |
| `lighttrans\` | 源码：窗口、热键、配置、开机启动 |
| `assets\` | 界面与图标（`index.html`、`icon.ico`） |
| `config.json` | **快捷键 / 窗口尺寸 / 翻译接口配置** |
| `requirements.txt` | 依赖清单 |
| `.venv\` | 虚拟环境，自动生成，可以随时删掉重建 |
| `README.md` | 本文件 |

不打包，直接跑源码。整个文件夹可以整体拷到别处用。

### 桌面快捷方式

**程序不会自动往桌面放快捷方式**（`translation_start.bat` 也不会）。想要就手动建一次：

- 命令行：`./.venv/Scripts/python.exe app.py --shortcut`（加 `--force-shortcut` 强制重写）
- 生成过程是纯 Python 调系统 COM 接口，毫秒级、不弹窗，不依赖 PowerShell
- 手动建过之后，它不会自己更新：把整个文件夹挪了位置，要再用上述命令重建一次

### 跑起来

- **双击 `translation_start.bat`** —— 就这样
- 命令行：

```bash
./run.sh              # 正常启动（pythonw，没有控制台窗口）
./run.sh --hidden     # 静默启动，后台待命，不创建托盘
./run.sh --devtools   # 带开发者工具
./run.sh --selftest   # 自检：写 selftest.log（无界面）
./run.sh -c           # 用 python.exe 跑，能看到控制台输出和报错
```

`translation_start.bat` 也认参数：`translation_start.bat --hidden`。首次创建环境时可用 `PYTHON=/path/to/python ./run.sh` 指定基础解释器，程序始终运行在自己的 `.venv`。

移动工具箱后，可以用新目录里的 `translation_start.bat` 启动。
## config.json

```jsonc
{
  "window": { "width": 500, "height": 600 },

  "shortcuts": {
    "global":    "Ctrl+Alt+2",
    "hide":      "Esc",
    "focus":     "Ctrl+F",
    "translate": "Ctrl+Enter",
    "clear":     "Ctrl+L",
    "copy":      "Ctrl+Shift+C"
  },

  "providers": [
    { "id": "mymemory", "name": "MyMemory", "enabled": true, "url": "https://api.mymemory.translated.net/get" }
  ]
}
```

- 支持 `//` 行注释和 `/* */` 块注释，注释掉整行后残留的逗号也没关系
- `shortcuts` 的键名可用字母、数字、`Esc`、`Enter`、`Space`、`F1`-`F12`、方向键等，配合 `Ctrl` / `Alt` / `Shift` / `Win`
- `providers` 按列表顺序尝试启用的服务。`enabled: false` 可停用指定服务。
- `window` 只影响首次启动的尺寸，之后会记住你拖出来的大小和位置

## 技术说明

底层：**pywebview**（窗口）+ **pynput**（全局热键）；托盘统一归 Manager。

- 界面用系统自带的 **WebView2**（Edge 内核）渲染，不打包 Chromium。
- 页面由 `127.0.0.1` 上的临时端口提供（不是 `file://`），来源正常，`fetch` 跨域和 `navigator.clipboard` 都按普通网页的规矩来；翻译请求仍由页面直接调用接口（接口返回 `Access-Control-Allow-Origin: *`，用 `text/plain` 发 JSON 可免预检），没有本地代理服务。
- 无边框窗口：给窗口补上 `WS_THICKFRAME`，再把 `WM_NCCALCSIZE` 算出来的边框扣掉，客户区铺满整个窗口；拖动顶栏和拖边改大小都是把 `WM_NCLBUTTONDOWN` 丢给系统自己的移动 / 缩放循环，所以跟原生窗口一样跟手。
- 全局热键走键盘钩子，自己判虚拟键码，主键盘和小键盘的数字都认，不依赖快捷方式的「快捷键」属性。
- 单实例：重复启动只会把已有窗口叫出来（命名互斥体 + 命名事件）。
- 窗口位置与偏好保存在 `%APPDATA%\LightTranslator`。

## 说明

- 需要系统里有 **Microsoft Edge WebView2 运行时**（Win11 自带；Win10 一般随 Edge 一起装好了，没有的话去微软官网装一个）。
- 启动时给 WebView2 传 `--no-sandbox --disable-gpu`：关掉硬件加速，并避开安全软件拦截 Chromium 子进程沙箱导致的崩溃（渲染进程一崩，界面会僵住）。
- 小键盘的数字需要 **NumLock 打开**（关掉时小键盘 2 实际发出的是「下方向键」，不会触发）。
- 配置文件解析失败时会保留原文件不动，只在日志里报错并使用默认配置。
- 要改界面：改 `assets\index.html`，重开一次程序就生效（页面是从磁盘读的，不用重新打包）。改行为：改 `lighttrans\` 里的模块，同样重开即可。
- 页面是从 `assets\` 目录实时读的，所以**别把 assets 删了**；`lighttrans\` 里是 Python 源码，改了立刻生效。
