"""把本工具的窗口、全局热键、本地服务和单实例串起来。"""

from __future__ import annotations

import os
import threading

from . import config as cfgmod
from . import native
from .hotkey import HotkeyManager
from .ipc import Ipc
from .model import Viewer
from .server import Server, open_externally
from .window import Ui


class App:
    def __init__(self, hidden: bool = False, debug: bool = False,
                 selftest: bool = False, initial: str | None = None) -> None:
        self.hidden = hidden
        self.debug = debug
        self.selftest = selftest
        self.initial = initial
        self.quitting = False
        self._notified = False

        self.cfg = cfgmod.Config.load()
        self.state = cfgmod.State.load()

        self.viewer = Viewer(self.cfg, self.state, on_change=self._on_change)
        self.ui = Ui(self)
        self.hotkey = HotkeyManager(self.show_window)
        self.server = Server(self.viewer, {
            "pick": self.pick_file,
            "show": self.show_window,
            "hide": self.hide_window,
            "quit": self.quit,
        })
        self.ipc = Ipc(self.cfg, self.state,
                       on_open=self.viewer.open_async,
                       on_show=self.show_window)

    # ---------------------------------------------------------------- 运行

    def run(self) -> None:
        url = self.server.start(self.selftest)
        cfgmod.log(f"[app] ui url = {url}")
        self.ipc.start()
        self.hotkey.start(self.cfg.get("global"))

        if self.initial:
            self.viewer.open_async(self.initial)   # 后台读，窗口先出来

        try:
            self.ui.start(url)                     # 阻塞到窗口关闭
        finally:
            self.shutdown()

    def shutdown(self) -> None:
        self.quitting = True
        self.save_bounds()
        self.hotkey.stop()
        self.ipc.stop()
        self.server.stop()

    # ---------------------------------------------------------------- 窗口

    def show_window(self) -> None:
        if self.quitting:
            return
        self.hidden = False
        self.ui.show()

    def hide_window(self) -> None:
        if self.quitting:
            return
        self.ui.hide()
        self.notify_once()

    def notify_once(self) -> None:
        """只记录第一次隐藏，避免每次收起都重复写提示。"""
        if self._notified:
            return
        self._notified = True
        cfgmod.log("[excel_shower] 窗口已隐藏，可用热键或启动入口唤出")

    def toggle(self) -> None:
        self.ui.show()

    def quit(self) -> None:
        if self.quitting:
            return
        self.quitting = True

        # 消息循环万一没退干净，兜个底，别留一个没有窗口的僵尸进程
        watchdog = threading.Timer(3.0, lambda: os._exit(0))
        watchdog.daemon = True
        watchdog.start()

        try:
            self.ui.hide()          # 先藏起来，退出时别闪一下
        except Exception:
            pass
        self.save_bounds()
        self.hotkey.stop()
        self.ipc.stop()
        self.ui.quit()

    # ---------------------------------------------------------------- 文件

    def pick_file(self, start_dir: str = "") -> str:
        return self.ui.pick_file(start_dir)

    def open_in_excel(self) -> None:
        doc = self.viewer.doc
        if doc and os.path.exists(doc.path):
            open_externally(doc.path)

    # ---------------------------------------------------------------- 配置

    def open_config(self) -> None:
        open_externally(str(self.cfg.path))

    def reload_config(self) -> None:
        self.cfg = cfgmod.Config.load()
        self.viewer.cfg = self.cfg
        self.hotkey.stop()
        self.hotkey.start(self.cfg.get("global"))
        self.viewer.rev += 1        # 让页面重新拉一次（快捷键可能变了）
        self.show_window()

    # ---------------------------------------------------------------- 内部

    def _on_change(self) -> None:
        self.ui.update_title()

    def save_bounds(self) -> None:
        """记住窗口位置，下次开在同一个地方。"""
        hwnd = getattr(self.ui, "_hwnd", 0)
        if not hwnd:
            return
        try:
            x, y, w, h = native.rect_of(hwnd)
            if w < 600 or h < 400:
                return
            self.state.bounds = (x, y, w, h)
            self.state.save()
        except Exception as e:
            cfgmod.log(f"[bounds] {e!r}")
