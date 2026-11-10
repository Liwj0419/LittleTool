# Manager 自己的规则

继承工具箱根目录的 `../AGENTS.md`。本文件只约束 `manager/`，产品特例按父级第 9 条处理；跨工具约定以父级文件为准，不要求其它工具读取本文件。

## 1. 实现工具箱统一托盘

- 按父级第 7 条由 Manager 创建托盘图标（`tray.py`），提供自己的退出入口。
- 各工具自身不创建托盘；旧的 `shim/` 和分发代码已移除，不再向其它工具注入 `sitecustomize.py` 或 `PYTHONPATH`。
- 不因新增工具改写对方源码、配置或启动脚本；其它来源的 Python 启动定制不得覆盖或删除。

## 2. 列表就是配置里的 `apps` 清单

- 工具箱根目录根据 Manager 所在目录的父目录确定，外层文件夹可以改名；不得检查固定目录名或依赖当前工作目录。
- `catalog.discover()` 逐条走 Manager 自己的 `config.json` 里的 `apps`：键是工具目录名，**顺序就是列表顺序**。这是用户明确要求的白名单，不是自动发现。
- **没写进 `apps` 的目录不显示；写进 `apps` 但目录里没有 `app.py` 的也不显示**，两种情况都只写 `app.log`，不占行、不报错弹窗。不要反过来「扫到就显示」，也不要给缺失项补占位行。
- 顺序只能来自 `apps` 的书写顺序：不得再按目录名、字母、状态或名称排序。`poll()` 用 `config.stamp()` 比对配置文件指纹，一旦变化就调用 `reload()` 并把整份清单送回页面（页面见到 `apps` 就整表重绘），所以改顺序、改名、增删项都不用重启。
- 展示名：`apps.<id>.name` → 工具自己的 `appName` → 目录名，取第一个非空。工具能力读取对方的 `tool.mode`，语义遵循父级第 6 条；未声明的旧工具按 `resident` 兼容。
- `apps` 条目里除 `name` 之外的字段不作数；`open` 这类旧开关已不再读取（形态一律以 `tool.mode` 为准）。
- 配置存在但无效（读不动、`tool.mode` 非法）时仍展示该行并写明原因，不得因为配置坏掉就悄悄漏掉一行。
- `apps` 里出现非法键（空串、`manager`、`.`/`__pycache__` 开头、带路径分隔符）直接忽略并记日志。

## 3. 窗口只拖动和缩放，不做全屏

- 默认尺寸取 `window.width/height`，最小尺寸取 `minWidth/minHeight`；用户可以自己拖边框改大小。
- **不提供最大化**：界面没有最大化按钮，双击顶栏不放大，`WM_SYSCOMMAND` 的 `SC_MAXIMIZE` 直接吞掉。不要为了「方便」把这条加回来。
- 无边框窗口右上角只有「最小化 / 关闭」两个按钮，规格按父级第 7 条；关闭 = 收进托盘继续待命（`behavior.closeToTray`）。

## 4. 不提供开机自启

- 不写 `HKCU\Software\Microsoft\Windows\CurrentVersion\Run`，不登记自启清单，也没有 `autostart.py`。用户要开机运行就自己放快捷方式。
- 不因为「以前有过」就在启动时改注册表；删掉的自启代码不要以任何理由恢复。
- 除窗口尺寸、快捷键、`apps` 清单外，Manager 不代写其它工具的业务配置。

## 5. 业务与 Win32 分开

- `native.py` 只放常量与函数签名，`processes.py` / `shell.py` / `window.py` 放行为。
- 新加系统能力时优先用标准库 `ctypes` / `winreg`，不要引第三方平台库。已经不管理自启，就别再引 `winreg`。

## 6. 装环境由 Manager 兜底

- Manager 中点「运行」时，由 `envsetup.ensure()` 补齐该工具自己的 `.venv` 和 `requirements.txt` 依赖；工具自己的启动脚本仍须能独立完成首次安装。
- 环境准备只在目标工具自己的 `.venv` 内执行，不分发垫片，不借用 Manager 的 `.venv` 运行子工具。
- 一律在后台线程里跑，不许阻塞 JS 桥的调用；进度只能通过 `installing` 里的文字告诉界面。
- 子进程必须带 `CREATE_NO_WINDOW`，安装过程不许闪控制台；失败信息写 `app.log` 之外，也要能在界面上看到。

## 7. 只提供通用管理能力

- 各工具都是独立进程，统一按工具目录、配置和启动参数管理；不加入 `kind="builtin"` 或按某个工具 ID 编写业务分支。
- `search_app` 与其它工具同样处理。它的热键、搜索、窗口和退出逻辑都由它自己负责，Manager 只注册本窗口自己的唤起键。
- 启动使用对方自己的解释器、工作目录与父级第 3 条参数；已有实例的唤出交给工具自己的单实例机制，不导入对方代码。
- 退出 Manager 默认保留其它工具进程；只有用户明确点击停止时才停止目标工具。
- 界面只展示「在跑 / 没跑 / 装环境中」和启停开关，不采集 CPU、内存等运行指标。
- 自检输出和界面用同一份清单与扫描逻辑，不维护手写工具名单。只有 Manager 自身行为或共同协议变化时才更新本工具 README。

## 8. 接入协议与验证

- `catalog.py` 统一读取 `appName`、`shortcuts.global` 和 `tool.mode`。后台工具不显示「打开窗口」，`tool.mode` 是唯一依据。
- `processes.start()` 按形态选择 `--show` / `--hidden`，停止时先请求本工具 `--quit`，必要时才结束它的进程树；进程树遇到其它工具自己的解释器就停止归属追踪，不能连带停止其它独立工具。
- 改接入、启停或清单逻辑时，在本目录执行 `.venv\Scripts\python.exe -m unittest discover -s tests -v` 和 `app.py --selftest`；测试使用临时工具，不启动真实工具、不修改系统设置。

## 9. 环境缺失一律弹窗

- 缺 Python 3.10+、版本过低、依赖装不上时，`manager_start.bat` 不打印控制台提示，而是用 `setup/python_missing.hta`（系统自带的 mshta）按 `missing` / `old` / `deps` 三种模式说明原因。
- 原因：父级第 3 条要求 `*_start.bat` 只能是纯 ASCII，而缺 Python 时没有任何解释器可以跑中文提示，所以中文文案放独立的 UTF-8 文件，bat 只传模式名。`%SystemRoot%\System32\mshta.exe` 不存在时才退回控制台英文提示 + `pause`。
- 提示里的「直接下载并安装」由 `setup/install_python.ps1` 完成：挑有安装包的版本（优先 `-PreferMinor 14` 成熟主线，避开 3.16 这类还没发布安装包的开发版目录）→ npmmirror / 华为云 / python.org 依次下载 → **`Get-AuthenticodeSignature` 校验且签名者必须是 Python Software Foundation** → `/passive InstallAllUsers=0 PrependPath=1` 用户级安装。签名不符必须中止，不得跳过校验。
- 脚本与 HTA 之间只通过 `%TEMP%\plug_python_install.state`（纯 ASCII 的 `key=value`）通信，状态里不写中文；界面文案由 HTA 按阶段生成。HTA 12 秒读不到状态即判定 PowerShell 起不来（组策略锁 ExecutionPolicy 时就是这种情况）。
- WebView2 缺失不走这条路：那时 Python 已可用，由 `manager/webview2.py` 直接弹 `MessageBoxW`（可一键打开下载页）并写 `app.log`。启动脚本不参与运行时检测。

## 10. 批处理里不要写回跳

- `manager_start.bat` 是 LF-only。cmd 对 LF 批处理的**后向 `goto`** 不可靠，实测会报「找不到批处理标签」，参数识别因此用单行 `echo %* | findstr ... && set`；新增逻辑只用向前 `goto`。
- 找解释器时除了 `py -3` 和 `python`，还要扫 `%LOCALAPPDATA%\Programs\Python\Python3*` 与 `%ProgramFiles%\Python3*`：刚装完时 explorer 的 PATH 可能还没刷新，只认 PATH 会导致「装完还是提示没装」。
- 改动启动脚本后要确认：纯 ASCII、无 BOM、无 CRLF，并且 `--selftest` 能返回退出码。
