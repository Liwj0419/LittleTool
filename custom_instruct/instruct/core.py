"""把本工具的窗口、热键与指令执行串起来。"""

from __future__ import annotations

import os
import threading

from . import actions
from . import config as cfgmod
from . import store as storemod
from .hotkey import HotkeyManager
from .window import MIN_HEIGHT, MIN_WIDTH, Ui


class App:
    def __init__(self, show_at_start: bool = False, debug: bool = False,
                 selftest: bool = False):
        # 正常启动显示窗口，--hidden 时只在后台待命。
        self.show_at_start = show_at_start or selftest
        self.debug = debug
        self.selftest = selftest
        self.quitting = False

        self.cfg = cfgmod.Config.load()
        self.store = storemod.load(self.cfg)

        self.ui = Ui(self)
        self.hotkey = HotkeyManager(self.show_window)

    # ---------------------------------------------------------------- 运行

    def run(self) -> None:
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

    def window_size(self) -> tuple[int, int]:
        """窗口大小只来自 config.json 的 window。"""
        return max(MIN_WIDTH, int(self.cfg.width)), max(MIN_HEIGHT, int(self.cfg.height))

    def show_window(self) -> None:
        if self.quitting:
            return
        self.show_at_start = True
        # 每次唤出都重读一遍 config.json，改完不用重启
        self._reload_table()
        self.ui.show(reset=True)

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

    # ---------------------------------------------------------------- 执行

    def execute(self, entry_id=None, query: str = "", moved: bool = False) -> dict:
        """页面按了回车：决定跑哪一条，然后交给 actions。"""
        entry = None

        # 手动挪过选择就尊重它：这样 "pm" 打开文件夹、"svn commit pm" 提交可以并存
        if moved and entry_id is not None:
            entry = self.store.get(entry_id)
        else:
            entry = self.store.exact(query)
            if entry is None and entry_id is not None:
                entry = self.store.get(entry_id)

        if entry is None:
            if self.cfg.flag("beep_on_miss"):
                actions.beep()
            return {"ok": False, "message": f"没有匹配「{query}」的指令"}

        cfgmod.log(f"[run] {entry.search_text} => {entry.path}")

        if entry.is_svn:
            if not os.path.isdir(entry.path):
                return {"ok": False, "message": f"文件夹不存在：{entry.path}"}
            missing = actions.tortoise_missing()
            if missing:
                return {"ok": False, "message": missing}
            # 先把窗口收起来，别让它盖在 TortoiseSVN 的窗口上面
            self.hide_window()
            error = actions.tortoise(entry.command, entry.path)
            if error:
                self.ui.show(reset=False)  # 真出错了再把窗口露回来报信
                return {"ok": False, "message": error}
            return {"ok": True}

        if not os.path.exists(entry.path):
            return {"ok": False, "message": f"路径不存在：{entry.path}"}

        error = actions.open_path(entry.path)
        if error:
            return {"ok": False, "message": error}
        self.hide_window()
        return {"ok": True}

    # ---------------------------------------------------------------- 配置

    def _reload_table(self) -> None:
        """只重读配置和数据表，不动热键。

        show_window 是在热键回调线程里跑的，那种场合重启键盘钩子会出问题。
        """
        self.cfg = cfgmod.Config.load()
        self.store = storemod.load(self.cfg)

    def reload_config(self) -> dict:
        self._reload_table()
        self.hotkey.update(self.cfg.get("global"))
        self.ui.send_state()
        return self.ui.state()

    def open_config(self) -> None:
        self._open(self.cfg.path)

    @staticmethod
    def _open(path) -> None:
        try:
            os.startfile(str(path))  # noqa: S606
        except Exception as e:
            cfgmod.log(f"[open] {e!r}")
