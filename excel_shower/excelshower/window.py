"""pywebview 窗口：装载 assets/index.html，管显示/隐藏/标题/文件选择框。

踩过的坑（别再踩）：

* ``boot()`` 回调跑在 **GUI 线程**上，里面调 ``window.title =`` 会 Invoke 回 GUI 线程，
  自己等自己 → 窗口"未响应"+整片白。所以标题一律用 Win32 ``SetWindowTextW`` 改。
* pywebview 自己算的居中坐标在多屏 + 高 DPI 下会把窗口丢到屏幕外，
  所以位置也用 Win32 自己摆（``native.place``）。
* ``window.restore()`` 实际是"最大化/还原"来回切，还原最小化要用 ``IsIconic`` +
  ``ShowWindow(SW_RESTORE)``（``native.restore``）。
"""

from __future__ import annotations

import os
import threading

import webview

from . import config as cfgmod
from . import native

# 描述里只能用字母/数字/下划线/空格（pywebview 的过滤器校验很严）
FILE_TYPES = (
    "表格文件 (*.xlsx;*.xlsm;*.csv;*.tsv;*.txt)",
    "Excel 工作簿 (*.xlsx;*.xlsm)",
    "CSV 和 TSV (*.csv;*.tsv;*.txt)",
    "所有文件 (*.*)",
)


class Ui:
    def __init__(self, core) -> None:
        self.core = core
        self.window = None
        self._hwnd = 0
        self._title = ""

    # ---------------------------------------------------------------- 启动

    def start(self, url: str) -> None:
        cfg = self.core.cfg
        icon = cfgmod.assets_dir() / "icon.ico"

        self.window = webview.create_window(
            cfgmod.APP_NAME,
            url=url,
            width=max(600, cfg.width),
            height=max(400, cfg.height),
            min_size=(max(600, cfg.min_width), max(400, cfg.min_height)),
            background_color="#ffffff",
            text_select=True,          # 单元格里的字可以像网页一样选中
            hidden=True,               # 先摆好位置再显示，避免闪一下
        )
        self.window.events.closing += self._on_closing
        self.window.events.loaded += self._on_loaded

        webview.start(
            self._boot,
            self.window,
            debug=self.core.debug,
            icon=str(icon) if icon.exists() else None,
        )

    def _boot(self, *_args) -> None:
        """GUI 起来之后：摆位置、改标题、必要时显示。

        pywebview 会把窗口对象当参数传进来，这里已经自己持有了，忽略即可。
        """
        try:
            win = self.window
            win.events.loaded.wait(15)
            self._hwnd = self._handle()
            cfgmod.log(f"[boot] shown, hwnd={self._hwnd}")

            cfg = self.core.cfg
            native.place(self._hwnd, self.core.state.bounds,
                         cfg.width, cfg.height)
            self.update_title()

            if self.core.hidden:
                self.hide()
            else:
                self.show()
            cfgmod.log("[boot] done")
        except Exception as e:
            cfgmod.log(f"[boot] {e!r}")

    def _handle(self) -> int:
        """拿窗口句柄：优先问 pywebview，问不到就按进程号找。"""
        win = self.window
        try:
            handle = getattr(getattr(win, "native", None), "Handle", None)
            if handle is not None:
                value = int(handle.ToInt64())
                if value:
                    return value
        except Exception:
            pass
        return native.find_hwnd()

    # ---------------------------------------------------------------- 显示

    def show(self) -> None:
        win = self.window
        if win is None:
            return
        native.restore(self._hwnd)
        try:
            win.show()
        except Exception as e:
            cfgmod.log(f"[show] {e!r}")
            return
        self.update_title()
        # 键盘焦点也一起拿过来，否则呼出窗口后按 Ctrl+F / 方向键没反应。
        # 窗口刚 show 出来时 WebView2 的内容子窗口还没就绪，一次不一定生效，
        # 所以隔一会儿再补一次（native.focus 会把焦点交给内容窗口，不是窗体）。
        native.focus(self._hwnd)
        self._focus_later(0.12)

    def _focus_later(self, delay: float) -> None:
        def run() -> None:
            try:
                if self.window is not None and native.is_visible(self._hwnd):
                    native.focus(self._hwnd)
            except Exception as e:
                cfgmod.log(f"[focus] {e!r}")

        timer = threading.Timer(delay, run)
        timer.daemon = True
        timer.start()

    def hide(self) -> None:
        if self.window is None:
            return
        try:
            self.window.hide()
        except Exception as e:
            cfgmod.log(f"[hide] {e!r}")

    def _on_closing(self) -> bool:
        """点 × / Alt+F4：不是退出，只是收进托盘。"""
        if self.core.quitting:
            return True
        threading.Timer(0.05, self.core.hide_window).start()
        self.core.notify_once()
        return False

    # ---------------------------------------------------------------- 标题

    def update_title(self) -> None:
        name = self.core.viewer.doc.name if self.core.viewer.doc else ""
        title = f"{name} - {cfgmod.APP_NAME}" if name else cfgmod.APP_NAME
        if title == self._title:
            return
        self._title = title
        native.set_title(self._hwnd or self._handle(), title)

    # ---------------------------------------------------------------- 文件框

    def pick_file(self, start_dir: str = "") -> str:
        """系统原生"打开文件"对话框（走 pywebview，不引入 tkinter）。"""
        win = self.window
        if win is None:
            return ""
        directory = start_dir if start_dir and os.path.isdir(start_dir) else ""
        try:
            result = win.create_file_dialog(
                webview.FileDialog.OPEN,
                directory=directory,
                allow_multiple=False,
                file_types=FILE_TYPES,
            )
        except Exception as e:
            cfgmod.log(f"[pick] {e!r}")
            return ""
        if not result:
            return ""
        if isinstance(result, (list, tuple)):
            return str(result[0]) if result else ""
        return str(result)

    # ---------------------------------------------------------------- 退出

    def quit(self) -> None:
        """结束 GUI 消息循环。

        pywebview 的 destroy() 走的是 Form.Close()，在隐藏过的窗口上不一定能真正
        结束 Application.Run()，所以直接让消息循环退出，并留个兜底。
        """
        win = self.window
        if win is None:
            return
        try:
            import clr  # noqa: F401  pythonnet 由 pywebview 的 winforms 后端带进来

            from System import Action
            from System.Windows.Forms import Application

            win.native.BeginInvoke(Action(Application.ExitThread))
        except Exception as e:
            cfgmod.log(f"[quit] {e!r}")
            os._exit(0)

    # ---------------------------------------------------------------- 页面加载完

    def _on_loaded(self) -> None:
        cfgmod.log("[ui] loaded")
