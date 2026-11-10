"""本工具自己的窗口、全局唤起键与配置。"""

from __future__ import annotations

import os
import threading

from . import config as cfgmod
from .hotkey import HotkeyManager
from .window import Ui


class App:
    def __init__(self, hidden: bool = False, debug: bool = False, selftest: bool = False):
        self.hidden = hidden
        self.debug = debug
        self.selftest = selftest
        self.quitting = False

        self.cfg = cfgmod.Config.load()

        self.ui = Ui(self)
        self.hotkey = HotkeyManager(self.show_window)

    # ---------------------------------------------------------------- 运行

    def run(self) -> None:
        # 桌面快捷方式仅在用户显式运行 app.py --shortcut 时创建。
        self.hotkey.start(self.cfg.get("global"))
        try:
            self.ui.start()  # 阻塞到窗口销毁
        finally:
            self.shutdown()

    def shutdown(self) -> None:
        self.quitting = True
        self.hotkey.stop()
        self.ui.stop()

    # ---------------------------------------------------------------- 窗口

    def show_window(self, reset: bool = True) -> None:
        if self.quitting:
            return
        self.hidden = False
        self.ui.show(reset)

    def hide_window(self) -> None:
        self.ui.hide()

    def quit(self) -> None:
        if self.quitting:
            return
        self.quitting = True

        # 消息循环万一没退干净，兜个底，别留一个没有窗口的僵尸进程
        watchdog = threading.Timer(3.0, lambda: os._exit(0))
        watchdog.daemon = True
        watchdog.start()

        try:
            self.ui.hide()  # 先藏起来，退出时别闪一下
        except Exception:
            pass
        self.ui.quit()

    # ---------------------------------------------------------------- 配置

    def reload_config(self) -> None:
        self.cfg = cfgmod.Config.load()
        self.hotkey.update(self.cfg.get("global"))
        self.ui.send_config()
        self.show_window(reset=False)

    def open_config(self) -> None:
        try:
            os.startfile(self.cfg.path)  # noqa: S606
        except Exception as e:
            cfgmod.log(f"[open] {e!r}")
