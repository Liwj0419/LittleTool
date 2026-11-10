# read_text 专项规则

继承工具箱根目录的 `../AGENTS.md`。本文件只约束 `read_text/` 的产品特例，按父级第 9 条处理；共同约定不得自行免除，其余条款照旧执行。

---

## 1. 形态：没有主窗口

它是常驻取字工具，不是"打开一个窗口干活"的应用。

- 唯一窗口是**覆盖层**，平时隐藏（`hidden=True`），只在按住热键锁定目标后短暂出现。
- 因此 `../AGENTS.md` 第 6 条里的"窗口默认大小"没有实际对象，`config.json` 用覆盖层外观参数代替。
- `Esc` 关闭覆盖层 = 回到后台，不退出进程。本工具不创建托盘，正常退出通过自己的命令行入口完成，不依赖 Manager。
- `app.py --quit` 独立退出，`app.py --reload` 重读配置；重复启动及 `--show` 只保持后台待命，不触发取字，取字仍由自己的唤起键触发。
- 也保留 `--hidden` 参数（启动即后台），是为和同仓库其它工具保持同一套入口约定。

## 2. 覆盖层用「截图当背景」，不要依赖窗口透明

```python
frameless=True, on_top=True, shadow=False, resizable=False,
min_size=(1, 1), easy_drag=False, text_select=True
```

- **不要打开 `transparent=True`**。实测在 `url` 加载本地页面 + 隐藏后再显示的组合下，WebView2 首次出现会先铺一层黑/白底色，
  用户会看到一块灰板盖住原图。改成把目标区域的截图 `data:image/png;base64` 铺成页面背景，
  视觉上和原图一致，且完全可控。代价是窗口必须严丝合缝盖住目标区域（`SetWindowPos` 已经能做到）。
- 因此**取字期间目标窗口必须是静态的**：视频、滚动、动画都会让背景图和真实画面对不上。这是有意的取舍。
- 页面里的文字用 `color: transparent`：字不画出来，只留一个透明的命中盒 + 选中高亮（见第 5.1 节）。
  不要为了"看得见"去改文字颜色——那样会盖住背景。
- 背景图走 `evaluate_js` 传 data URL（实测 1000×420 约 102 KB，传输 6 ms）。不要改成写临时文件再用相对路径，
  页面是从 http server 加载的，读不到 `%APPDATA%` 下的文件。
- **大图必须分块灌**（`__bgBegin` / `__bgPush` / `__bgEnd`，段长 `BG_CHUNK = 256 KB`）。
  单条几 MB 的脚本会让 WebView2 的 renderer 长时间不响应，这段时间页面里的
  keydown / mousedown 只能排队，用户看到的就是「图出来了、提示条也在，但按键和鼠标全没反应」。
  实测 3.1 MB 的 data URL 分 12 段灌完 176 ms（整屏 4K 截图就是这个量级）。
- `min_size` 必须显式设成 `(1,1)`：pywebview 默认 `(200,100)`，比它小的贴图会被撑大，背景随之被拉伸错位。
- `place()` 在原生窗口还没建好时会静默返回 False（`hwnd()` 拿到 0），此时窗口还停在创建时的 320×200。
  只在启动瞬间的极端情况下会出现（正常是热键触发，早就建好了），排查「背景被拉伸」时先看日志有没有这一行。

## 2.1 坐标：物理像素 → CSS 像素

- 窗口按**物理像素**摆放（`SetWindowPos`），页面按 **CSS 像素**排版，两者差一个缩放比。
- 缩放比**优先用 Python 传进来的截图尺寸**（`__render` 的 `bgW/bgH`），
  其次才是 `图片自然宽度 / window.innerWidth`，`devicePixelRatio` 只兜底。
  不能只靠 `naturalWidth`：`__render` 可能在背景图还没解码完时就到了，那时它是 0，
  退回 `dpr` 会把缩放比算错（WebView2 里 dpr 不一定等于系统缩放），
  结果是所有字块整体错位——图看着是对的、字一个都点不中，表现为「拖选没反应」。
- 字块存两份坐标：`px/py/pw/ph` 是 OCR 给的物理像素，`x/y/w/h` 是 `layout()` 换算出的 CSS 像素。
  `resize` 和背景图 `load` 都调一次 `layout()`，窗口尺寸晚于首次渲染才定下来时能自纠正。
- 所有 `span` 的 `left/top/width/height/fontSize` 都要乘这个系数；`dataset` 里仍存物理坐标，拼接逻辑不受影响。

## 3. 坐标一律用物理像素

这条最容易出错，改代码前先读：

- 进程启动第一件事调 `SetProcessDpiAwareness(2)`（`screen.enable_dpi_awareness()`），必须在 `import webview` 之前。
- **不要用** `create_window` 的 `x/y/width/height` 定位。实测 150% 缩放下，传 `(200,200,400,250)`
  实际得到 `(300,300,578,319)`——位置被乘了 1.5，尺寸还差几十像素。窗口位置一律走 `SetWindowPos`。
- `SetWindowPos` / `GetWindowRect` / `GetAncestor` 这些必须显式声明 `argtypes` 和 `restype`。
  不声明时 64 位句柄会被截成 4 字节，调用**静默失败**（返回 False、`GetLastError` 还是 0），
  现象是"窗口位置纹丝不动"，很容易误判成 pywebview 在跟自己抢。
- 「目标矩形」来自 `DwmGetWindowAttribute(DWMWA_EXTENDED_FRAME_BOUNDS)`，比 `GetWindowRect` 少一圈透明拖拽边。
- **锁定目标 = 鼠标下的窗口，不是焦点窗口**：`WindowFromPoint` → `GetAncestor(GA_ROOT)`。
  不要去查 `GetForegroundWindow`，用户是按「鼠标停在哪个图上」来表达意图的。
- 鼠标下的窗口如果铺满屏幕（最大化 / 无边框全屏），算出来的矩形就等于整屏——
  这是**正确结果**，不是 bug。为了让人一眼看出来，提示条要写上区域尺寸，
  日志要带上 `类名 + 标题 + 坐标`（`window_info_at_cursor()` 返回这些）。
- 传 `ignore_hwnd` 把覆盖层自己排除掉：覆盖层是置顶的，刚 `hide()` 的瞬间
  `WindowFromPoint` 还可能命中它，否则会「锁到自己」，表现出来就是越锁越大。
- OCR 坐标是在**放大后的图**上量的，`ocr.recognize()` 里用实际缩放比（由尺寸反推，不要用整数倍）
  折回原图坐标，别在别处再除一次。

## 4. 识别引擎不引第三方包

- 用系统自带的 `Windows.Media.Ocr`，通过**常驻 PowerShell 子进程**桥接（`assets/ocr_bridge.ps1`）。
- 原因：`../AGENTS.md` 第 8 条要求标准库优先、可跨平台；`winsdk` / `winrt-*` 是 Windows-only 第三方包，
  为它单独破例不值得，而 PowerShell 是系统自带、零依赖。
- 桥进程只启动一次（`Ocr.start()` 在后台线程预热），之后每次识别几十毫秒。
  不要改成"每次识别起一个进程"，那样每次要多付几百毫秒。
- 协议是行文本，不是 JSON：启动时一句 `ready`；之后
  `in: <请求号>\t<图片绝对路径>` → `ok\t<请求号>` + 每块一行 + `end`，
  或 `err\t<请求号>\t<消息>`。字段用 `\t` 分隔，行尾那个字段（文字本身）必须放最后。
  请求号是**必须的**：超时/异常之后桥进程里可能还压着上一轮的结果，
  没有请求号就只能把别人的结果当成自己的，串行错位且完全无声。
- 有英文语言包时，桥可在完整的原结果后发 `english`，再发英文结果的文字块。Python 按位置匹配，
  只用能补回路径符号或字符的结果替换 ASCII 文字块。英文复识别失败时保留已收到的原结果。
- 桥脚本必须**纯 ASCII**：PowerShell 5.1 读无 BOM 的 `.ps1` 按 ANSI 解释，中文会乱码甚至语法错误。
  所以桥自己的报错是英文（`temp image not found` 之类），中文在 `ocr.py` 的 `_friendly()` 里翻。
- 交给桥的临时图必须写在 `cfgmod.temp_dir()`（`%TEMP%`），**不要写 `data_dir()`**。
  `%APPDATA%\read_text` 在一些环境（沙箱 / 重定向 / 漫游配置）里写入只有写它的进程看得见，
  桥进程（另一个 powershell）打开时就是「文件不存在」。实测 `.venv` 里的 python 写 `%APPDATA%`
  就这样，写 `%TEMP%`、工作区、`%USERPROFILE%` 都正常。`_recognize_file()` 里保留
  「换下一个目录再试一次」的退路，但正常路径就是 `%TEMP%`。
- 读图**不要用** `StorageFile.GetFileFromPathAsync`：路径形态/ACL/共享方式稍有不顺，
  它就只回一句 `使用"1"个参数调用"Wait"时发生异常:"发生一个或多个错误。"`，
  真实原因全被 `AggregateException` 吃掉。现在用 `[System.IO.File]::Open` + `AsRandomAccessStream`，
  `Await()` 里把 AggregateException 层层剥到根因并带上 HRESULT。
- 解码时显式要 `Bgra8` + `Ignore` 的 `SoftwareBitmap`：这是 OCR 唯一保证接受的像素格式，
  省得某些 PNG 直接喂进去被引擎拒。
- `err` 之后 `ocr.py` 会**重启桥进程重试一次**，但确定性失败（`_NO_RETRY_HINTS`：
  临时图片不在/是空的、WIC 解码失败 `0x88982f*`、缺 OCR 组件）不重试——那只是让用户多等一轮。
  超时视为桥状态不明，直接杀掉重拉。
- 桥进程的 stderr 以前 `DEVNULL`，现在读出来写进 `app.log`（前 40 行）；
  万一 PowerShell 先吐版权标语，等 `ready` 的循环会跳过它并记一行日志，不当失败。


## 5. 交互：选中之后按 Ctrl+C 才复制

- 默认 `behavior.copyOnRelease = false`：`mouseup` 只结束选区，**不复制、也不收起窗口**，
  顶部提示改成常驻的「已选中 N 个字符，按 Ctrl+C 复制」。
- 只有两件事能让覆盖层消失：**复制成功**、**按 Esc**（`shortcuts.cancel`）。
  `behavior.closeOnOutsideClick` 默认也是 `false`，点空白不关——用户明确要求「没复制、没按 Esc 之前不关闭」。
- 复制结果按行拼接：行内按 `x` 排序，间隙大于「字宽 × `ocr.joinGap`」才补空格——
  中文单字块之间不会多出空格，被拆碎的英文单词还能重新粘回去。
- 页面里的快捷键（复制 / 退出 / 全选）由 `matchShortcut()` 解析 `config.json` 里的字符串，
  不要在 `index.html` 里硬编码 `e.key === 'c'`，否则配置项就成了摆设。

## 5.1 选区自己做，**不要**交给浏览器

`mousedown` 里 `preventDefault()`，`.w` 上 `user-select: none`，原生文本选择完全不用。
原因是实测的两个毛病，都出在浏览器自己的命中判定上：

- 它按**字形排版**算命中：OCR 给的盒子只有 1～3px 高的淡字/小字几乎点不到；
- 拖到行尾之外，端点会被吸到**内容流的下一个位置**（下一行开头 / 文档末尾），
  于是一下多选一大片。用户原话：「选中到末尾，总是会把其余的一大片都一起选中上」。

现在是几何选区（`assets/index.html`）：

- 每个字块记在 `WORDS`（阅读顺序：行号 → 行内 `x`），命中靠**点到矩形的距离**
  （`distTo()`），点偏了也能吸附最近的——`SNAP = 48` CSS 像素只用在 `mousedown` 上，
  用来区分「点空白」和「点字」。
- 命中 ASCII 文字块后，在块内按字符选择。字符边界用字宽估算，命中时采样截图里的字缝校准。
  选区端点按字符编号，高亮只画所选区间。路径片段直接拼接，路径两侧沿用原字块宽度判断空格。
- 拖动的端点**不设距离上限**，但有**行内锁定**：鼠标还在起始词那条的高度带里
  （`y` 在 `anchor.y ± max(2, h*0.35)`），只在起始行里找端点。顺着行往右拖绝不会跨行，
  上下离开这条带子才切到别的行（多行选择照旧）。
- 高亮是 `.w.sel` 的 `background`，`padding:1px + margin:-1px` 让色块比字块宽出一圈，
  1px 高的字也看得出选中了。
- 方向键可以按字/按行微调端点（`nextLine()`），拖不准时兜底。
- 复制/全选走 `__serialize()` / `__selectAll()`，都读 `WORDS` + 选区区间，不再用 `Range`。

## 5.1 按下就要有反馈

- 顺序必须是：**先截图 → 再 show → 再灌内容**。反过来的话，截图会把自己那层窗口截进去。
- `show()` 之后必须先 `wait_ready()` 再碰页面。隐藏状态下 `evaluate_js` 可能不返回，
  页面脚本没就绪时调用 `__message` / `__render` 会静默丢掉，表现为"窗口亮了但没有提示"。
- 立刻显示「正在截取文字中…」（`ms=0`，常驻），识别完再替换成操作提示。
  **不要给提示条设自动消失**——用户要求"关闭了才消失"，`showHint` 的 `ms=0` 就是为这个留的。
- 识别失败、没识别到文字时，也留在覆盖层里显示原因（并提示按 Esc），不要直接收窗——
  此时窗口已经盖住原图，贸然关掉用户会以为程序卡了。

## 5.2 关闭覆盖层必须有 Python 侧的兜底（踩过坑）

页面里的 `keydown` 只在**覆盖层是前台窗口、且 renderer 活着**时才有用。实测出现过
「原图和提示条都在，但按键和鼠标全没反应，只能再按 Alt+F1」——用户视角就是卡死。所以：

- **取消键在 Python 里也监听一份，但只在覆盖层是前台窗口时才算数**
  （`core.App.cancel_hotkey` → `_on_cancel_hotkey`）。钩子本身是全机的，回调里要过两道闸：
  `overlay.visible` 且 `screen.foreground_is(hwnd)`。焦点在别的程序上时按 Esc 什么都不做——
  窗口内快捷键不许变成全局快捷键（根目录 `AGENTS.md` §6.1）。
  留这份的唯一意义是 **renderer 卡死**：页面里的 `keydown` 进不来，但覆盖层仍然是前台窗口，
  这时由 Python 侧直接收窗。页面那份是正常路径。两条路都走 `on_cancel(source)`，日志里能看出是谁关的。
  取消键配置是 `shortcuts.cancel`，`reload_config()` 时要跟着 `hotkey.update()` 一起更新。
- **置顶 ≠ 拿到前台**。`window.show()` 里的 `Activate()` 受 Windows 前台锁限制，失败是静默的；
  `force_foreground()` 必须把**当前前台线程**和**窗口所属线程**的输入队列都挂到当前线程上
  （只挂前台线程是没用的，窗口的线程没挂上，`SetForegroundWindow` 照样被拒）。
  抢完用 `foreground_is()` 校验，抢不到要记日志。
- **`evaluate_js` 一律走 `Overlay._eval()` 的超时封装**：pywebview 内部是「阻塞 Invoke +
  信号量无超时等待」，页面忙或窗口状态异常时调用线程会永久卡住，连 `_busy` 一起占死。
  工作线程是 daemon，超时只丢弃这一次结果。
- **`hide()` 默认 `wait=True`**（截图前必须真的收起来），但 Esc / 复制这条路径用
  `hide(wait=False)`：先翻 `_visible` 标记，再在后台线程里 `window.hide()`。
  在按键回调里同步等 UI 线程，就等于「按了 Esc 却回不来」。
- `_capture()` 开头记 `epoch`，识别完再比一次：期间用户按了 Esc 就别把内容灌回去。
- 热键是最高优先级的逃生口：`_busy` 卡住超过 8 秒时，热键仍然可以直接收窗（`on_cancel("热键接管")`）。

## 6. 配置项对应关系

`config.json` 里的键和代码里的读取位置必须一一对应，改键名时两处一起改：

| config.json | 读取处 |
| --- | --- |
| `shortcuts.global` | `core.App.run` → `hotkey.start` |
| `shortcuts.cancel` | `config.page()` → 页面按键；`core.App._cancel_spec()` → 取消钩子（仅覆盖层为前台时生效） |
| `shortcuts.copy` / `.all` | `config.page()` → 页面按键 |
| `overlay.selectionColor` / `hintColor` / `showHint` / `hintText` | `config.page()` → `index.html` |
| `ocr.upscale` | `core.App.__init__` → `Ocr()` |
| `ocr.joinGap` | `config.page()` → `index.html` 拼接逻辑 |
| `behavior.copyOnRelease` / `closeOnOutsideClick` | `config.page()` → `index.html` |

## 7. 自检要能不开界面跑完

`./run.sh --selftest` 必须只验证「找窗口 → 截图 → 识别 → 写剪贴板」这条链路，
不启动 pywebview、不注册热键、不弹任何窗口，结果写 `selftest.log` 后退出。
改识别或坐标逻辑时，先跑它。
