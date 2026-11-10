"""把本工具的窗口、热键与扫描串起来。"""

from __future__ import annotations

import json
import os
import threading

from . import config as cfgmod
from . import native, opener, scan
from .hotkey import HotkeyManager
from .window import Ui


class App:
    def __init__(self, hidden: bool = False, debug: bool = False, selftest: bool = False):
        self.hidden = hidden
        self.debug = debug
        self.selftest = selftest
        self.quitting = False

        self.cfg = cfgmod.Config.load()
        self.current_key: str | None = None
        self.current_files: list[dict] = []

        self.ui = Ui(self)
        self.hotkey = HotkeyManager(self.show_window)

    # ---------------------------------------------------------------- 运行

    def run(self) -> None:
        self.hotkey.start(self.cfg.global_specs())
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

    # ---------------------------------------------------------------- 扫描

    def theme(self) -> str:
        theme = self.cfg.theme
        if theme == "auto":
            return native.system_theme()
        return theme if theme in ("dark", "light") else "dark"

    def scan_source(self, key: str) -> dict:
        source = self.cfg.source(key)
        if source is None:
            return {
                "ok": False,
                "key": str(key),
                "name": "",
                "path": "",
                "files": [],
                "count": 0,
                "truncated": False,
                "elapsed": 0.0,
                "error": f"config.json 里没有编号为 {key} 的数据源",
            }

        result = scan.scan(source, self.cfg.search)
        if result["ok"]:
            self.current_key = str(key)
            self.current_files = result["files"]
        cfgmod.log(
            f"[scan] {result['name']} -> {result['count']} 个文件 "
            f"({result['elapsed']}s){' ' + result['error'] if result['error'] else ''}"
        )
        return result

    def rescan(self) -> dict:
        if self.current_key:
            return self.scan_source(self.current_key)
        first = self.cfg.first_source()
        if first:
            return self.scan_source(str(first["key"]))
        return {
            "ok": False,
            "key": "",
            "name": "",
            "path": "",
            "files": [],
            "count": 0,
            "truncated": False,
            "elapsed": 0.0,
            "error": "config.json 里还没有配置数据源",
        }

    def rescan_from_tray(self) -> None:
        """托盘里的「重新扫描」：没选过数据源就把窗口叫出来选。"""
        if self.current_key is None:
            self.show_window(reset=True)
            return
        self.ui.show(reset=False)
        self.ui.eval("window.__bridgeScanning&&window.__bridgeScanning();")
        result = self.rescan()
        payload = json.dumps(result, ensure_ascii=False)
        self.ui.eval(f"window.__bridgeScanResult&&window.__bridgeScanResult({payload});")

    # ---------------------------------------------------------------- 文件

    def open_file(self, path: str) -> dict:
        result = opener.open_path(path, self.cfg)
        if not result["ok"]:
            cfgmod.log(f"[open] 失败：{path} {result['error']}")
        return result

    def reveal(self, path: str) -> bool:
        return opener.reveal(path)

    # ---------------------------------------------------------------- 配置

    def reload_config(self) -> None:
        self.cfg = cfgmod.Config.load()
        self.current_key = None
        self.current_files = []
        self.hotkey.update(self.cfg.global_specs())
        self.ui.notify_theme()
        self.ui.send_config()
        self.show_window(reset=False)

    def open_config(self) -> None:
        try:
            os.startfile(self.cfg.path)  # noqa: S606
        except Exception as e:
            cfgmod.log(f"[open] 配置文件打不开：{e!r}")
