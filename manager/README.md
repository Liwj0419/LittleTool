# Manager

本工具箱里所有独立工具的展示和管理入口。工具箱根目录是 `manager/` 所在目录的父目录，外层文件夹名称不固定。

**列表只显示本工具 `config.json` 的 `apps` 里写明的软件**：没写进去的目录不出现，写了但目录里没有 `app.py` 的也不出现（只写日志）。顺序就是 `apps` 里的书写顺序。共同约定见 [根 AGENTS.md](../AGENTS.md)。

## 启动和退出

双击 `manager_start.bat`。首次运行自动创建本工具的 `.venv` 并安装 `requirements.txt`。

```bash
./run.sh                 # 显示窗口
./run.sh --hidden        # 后台待命，保留 Manager 托盘
./run.sh --show          # 唤出现有窗口，复用实例
./run.sh --reload        # 重读 Manager 配置、重新发现工具并刷新列表
./run.sh --quit          # 独立退出 Manager，保留其它工具进程
./run.sh --selftest      # 无界面自检，写 selftest.log 并返回退出码
./run.sh --devtools      # 开发者工具
./run.sh -c              # 使用控制台解释器，方便调试
```

默认 `Ctrl+Alt+0` 唤出 / 收起，`Esc` 和关闭按钮只隐藏窗口。托盘右键有「退出」，也可以使用命令行退出。

窗口不能最大化（没有最大化按钮，双击顶栏也不放大），但可以拖任意一条边或角改大小，最小尺寸由配置里的 `minWidth` / `minHeight` 兜住。

## 运行前提

**Python 3.10 或更高版本。** 双击启动脚本会自动建 `.venv`；没装 Python、版本低于 3.10、或依赖装不上时，会弹提示（`setup/python_missing.hta`）。

提示里可以直接点「直接下载并安装」，不用开浏览器：`setup/install_python.ps1` 会挑一个带 Windows 安装包的成熟版本（优先 3.14 主线），从 npmmirror → 华为云 → python.org 依次尝试下载，**校验 Authenticode 签名**（必须是 Python Software Foundation）后才按当前用户安装（`InstallAllUsers=0 PrependPath=1`，不需要管理员），界面上有进度条。签名不对就直接停下，不执行安装；下载或安装失败则退回「打开下载页」手动安装。

**Microsoft Edge WebView2 运行时。** 界面由它渲染：pywebview 只提供窗口和 JS 桥，自身不带浏览器内核。Windows 11 预装；Windows 10 大部分设备已有，但 Windows Server、LTSC 和长期离线的机器可能缺。启动前会先检测一次，缺失时弹系统提示，可直接打开官方下载页。

命令行检查运行时：

```bash
.venv/Scripts/python.exe -m manager.webview2           # 打印版本，缺了打印安装方式
.venv/Scripts/python.exe -m manager.webview2 --quiet   # 只看退出码：0 可用、1 缺失
```

## 列表与操作

- 只有 `config.json` 的 `apps` 里列出的工具会出现；缺目录、缺 `app.py` 的一律跳过，日志在 `app.log` 里写明原因。
- 展示名优先取 `apps.<id>.name`，为空时退回工具自己配置里的 `appName`。英文 ID 是工具目录名。
- 快捷键读取各工具自己的 `shortcuts.global`，Manager 不代管子工具热键。
- 状态只区分未运行、后台待命、窗口打开（不采集 CPU / 内存）。
- 「运行」开关负责启停。启动在后台补齐该工具自己的虚拟环境和依赖，安装进度与失败原因显示在界面中。
- 「打开窗口」按工具类型显示；齿轮打开配置，文件夹按钮或右键行打开工具目录。
- 配置不存在、损坏或工具类型无效时，仍保留该行并显示原因，修正后重新加载。
- 停止时先请求目标自己的 `--quit`；必要时才结束目标进程树。其它独立工具的解释器是进程归属边界，不会因它由目标工具启动而被连带停止。

`tool.mode` 由各工具自己的 `config.json` 声明：

| 值 | 普通启动 | 打开窗口 |
| --- | --- | --- |
| `resident` | `app.py --hidden`，静默待命 | `app.py --show` |
| `window` | `app.py --show`，显示窗口 | `app.py --show` |
| `background` | `app.py --hidden`，后台待命 | 不提供 |

旧工具未声明时暂按 `resident` 兼容。旧的 `apps.<id>.open` 已不再读取，形态只看 `tool.mode`。工具自身不创建托盘，Manager 不注入或分发托盘垫片。

## 配置

```jsonc
{
  "appName": "工具箱管理器",
  "tool": { "mode": "resident" },
  "window": { "width": 900, "height": 620, "minWidth": 740, "minHeight": 460 },
  "theme": "auto",
  "shortcuts": { "global": "Ctrl+Alt+0", "hide": "Esc" },
  "behavior": { "hideOnBlur": false, "pollMs": 1500, "closeToTray": true },
  "apps": {
    "search_app": { "name": "搜索应用" },
    "read_text": { "name": "屏幕取字" }
  }
}
```

`apps` 同时是清单和中文名：

- 加一行就出现在列表里，删一行就从列表里消失；**顺序即列表顺序**。
- 键必须与工具目录名一致；写了但目录里没有 `app.py` 的会被跳过（记日志，不弹窗）。
- 改完保存就生效：Manager 每次轮询会比对配置文件的指纹，发现变化就重读并整表重排（包含顺序、名称、增删项），不需要重启；按 `F5` 也可以立刻刷新。

Manager 不提供开机自启，也不写注册表 Run 项；需要开机运行请自己放快捷方式。

## 验证与日志

在本目录执行：

```bat
.venv\Scripts\python.exe app.py --selftest
.venv\Scripts\python.exe -m unittest discover -s tests -v
```

自检输出清单里每个工具的名称、形态、配置问题和运行状态，并列出「配置了但没找到 `app.py`」的条目。回归检查使用临时工具目录及模拟进程，不会启动真实工具、修改系统设置或改写业务配置。

日志位于 `%APPDATA%\PlugManager\app.log`。底层使用 pywebview、pynput、pystray；以源码和本工具自己的虚拟环境运行。
