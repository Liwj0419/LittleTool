"""把清单、进程、窗口、托盘串起来的应用本体。

Manager 只管「哪个软件在跑、能不能启停、配置在哪」，不实现任何具体功能：
应用启动器（search_app）现在是 工具箱根目录下的一个普通软件，和别的软件一个形状。
"""

from __future__ import annotations

import os
import threading

from . import catalog
from . import config as cfgmod
from . import envsetup
from . import processes
from . import native as n
from .hotkey import HotkeyManager
from .tray import Tray
from .window import Ui


class App:
    def __init__(self, hidden: bool = False, debug: bool = False,
                 selftest: bool = False):
        self.hidden = hidden
        self.debug = debug
        self.selftest = selftest
        self.quitting = False

        self.cfg = cfgmod.Config.load()
        self.apps = catalog.discover(self.cfg)
        self._by_id = {app.id: app for app in self.apps}
        self._stamp = cfgmod.stamp(self.cfg.path)  # 配置文件指纹，变了就重排
        self._busy: set[str] = set()
        self._installing: dict[str, str] = {}  # id -> 当前在做什么
        self._install_error: dict[str, str] = {}
        self._lock = threading.Lock()

        self.ui = Ui(self)
        self.tray = Tray(self)
        self.hotkey = HotkeyManager()
        self.hotkey.bind("global", self.toggle_window)

    # ---------------------------------------------------------------- 运行

    def run(self) -> None:
        self.hotkey.start(self._hotkey_specs())
        self.tray.start()
        try:
            self.ui.start()
        finally:
            self.shutdown()

    def _hotkey_specs(self) -> dict[str, str]:
        return {"global": self.cfg.get("global") or ""}

    def shutdown(self) -> None:
        self.quitting = True
        self.hotkey.stop()
        self.tray.stop()
        self.ui.stop()

    def quit(self) -> None:
        if self.quitting:
            return
        self.quitting = True
        watchdog = threading.Timer(3.0, lambda: os._exit(0))
        watchdog.daemon = True
        watchdog.start()
        try:
            self.ui.hide()
        except Exception:
            pass
        self.ui.quit()

    # ---------------------------------------------------------------- 状态

    def statuses(self, apps=None) -> dict:
        """一趟进程快照，算出每个软件是停着、在后台、还是窗口开着。"""
        apps = self.apps if apps is None else apps
        procs = processes.snapshot()
        out: dict[str, dict] = {}
        for app in apps:
            pids = processes.pids_of(app.path, procs)
            window = ""
            for pid in pids:
                window = processes.visible_window(pid)
                if window:
                    break
            if not pids:
                state, text = "stopped", "未运行"
            elif window:
                state, text = "window", "窗口打开"
            else:
                state, text = "background", "后台挂起"

            out[app.id] = {
                "running": bool(pids),
                "pids": pids,
                "window": window,
                "state": state,
                "text": text,
            }
            if app.config_error:
                out[app.id].update(state="error", text="配置异常", error=app.config_error)

        # 正在装环境的那几行，状态列说人话：装到哪一步了
        with self._lock:
            notes = dict(self._installing)
        for app_id, note in notes.items():
            info = out.get(app_id)
            if info is not None:
                info.update(
                    state="installing",
                    text=note or "安装环境中",
                    running=False,
                )
        return out

    def state(self) -> dict:
        return {
            "apps": [app.page() for app in self.apps],
            "status": self.statuses(),
            "busy": sorted(self._busy),
            "installing": dict(self._installing),
            "installError": dict(self._install_error),
            "root": str(cfgmod.PLUG_ROOT),
            "config": str(self.cfg.path),
            "theme": self.cfg.theme,
            "pollMs": self.cfg.poll_ms,
            "shortcuts": self.cfg.shortcuts,
        }

    def poll(self) -> dict:
        """轻量刷新：配置没动就只回状态；配置动过就重读并按新顺序重排。"""
        if cfgmod.stamp(self.cfg.path) != self._stamp:
            try:
                return self.reload()  # 带 apps，页面整表重绘
            except Exception as e:
                cfgmod.log(f"[poll] 重读配置失败：{e!r}")
        return {
            "status": self.statuses(),
            "busy": sorted(self._busy),
            "installing": dict(self._installing),
            "installError": dict(self._install_error),
        }

    # ---------------------------------------------------------------- 开关

    def _app(self, app_id: str):
        app = self._by_id.get(app_id)
        if app is None:
            raise KeyError(app_id)
        return app

    def start(self, app_id: str, show: bool = False) -> dict:
        app = self._app(app_id)
        if app.config_error:
            return self._result(app, False, app.config_error)
        running = self.statuses([app]).get(app.id, {}).get("running", False)
        if not running:
            return self._install_then_start(app, show)
        if not show:
            return self._result(app, True, "已经在运行")

        with self._lock:
            self._busy.add(app.id)
        try:
            ok, message = processes.start(app, show=show)
        finally:
            with self._lock:
                self._busy.discard(app.id)

        if ok:
            with self._lock:
                self._install_error.pop(app.id, None)
        return self._result(app, ok, message)

    def _install_then_start(self, app, show: bool) -> dict:
        """没有 .venv：扔进后台线程装环境，装完自己启动，界面不用等这一下。"""
        with self._lock:
            already = app.id in self._installing
            if not already:
                self._installing[app.id] = "准备安装环境"
                self._busy.add(app.id)
                self._install_error.pop(app.id, None)

        if already:
            return self._result(app, True, "正在安装运行环境")

        threading.Thread(
            target=self._install_work, args=(app, show), name=f"env-{app.id}", daemon=True
        ).start()

        result = self._result(app, True, "正在安装运行环境，装好会自动启动")
        result["pending"] = True
        return result

    def _install_work(self, app, show: bool) -> None:
        def note(text: str) -> None:
            with self._lock:
                if app.id in self._installing:
                    self._installing[app.id] = text

        try:
            ok, message = envsetup.ensure(app, note)
            if not ok:
                cfgmod.log(f"[env] {app.id} 失败：{message}")
                with self._lock:
                    self._install_error[app.id] = message
                return

            note("启动中")
            ok, message = processes.start(app, show=show)
            if not ok:
                with self._lock:
                    self._install_error[app.id] = message
        except Exception as e:  # 线程里不能让异常悄悄溜走
            cfgmod.log(f"[env] {app.id} 异常：{e!r}")
            with self._lock:
                self._install_error[app.id] = f"安装出错：{e}"
        finally:
            with self._lock:
                self._installing.pop(app.id, None)
                self._busy.discard(app.id)

    def stop(self, app_id: str) -> dict:
        app = self._app(app_id)
        with self._lock:
            self._busy.add(app.id)

        def work() -> None:
            try:
                processes.stop(app)
            finally:
                with self._lock:
                    self._busy.discard(app.id)

        threading.Thread(target=work, name=f"stop-{app.id}", daemon=True).start()
        return self._result(app, True, "")

    def toggle(self, app_id: str, on: bool) -> dict:
        app = self._app(app_id)
        running = self.statuses([app]).get(app.id, {}).get("running", False)
        if on and not running:
            return self.start(app_id)
        if not on and running:
            return self.stop(app_id)
        return self._result(app, True, "")

    def start_all(self) -> dict:
        errors = []
        status = self.statuses()
        for app in self.apps:
            if status.get(app.id, {}).get("running"):
                continue
            result = self.start(app.id)
            if result.get("ok") is False:
                errors.append(f"{app.cn}：{result.get('message') or '启动失败'}")
        return {"ok": not errors, "message": "\n".join(errors), "state": self.state()}

    def stop_all(self) -> dict:
        targets = [a for a in self.apps]
        for app in targets:
            with self._lock:
                self._busy.add(app.id)

        def work() -> None:
            for app in targets:
                try:
                    processes.stop(app)
                finally:
                    with self._lock:
                        self._busy.discard(app.id)

        threading.Thread(target=work, name="stop-all", daemon=True).start()
        return {"ok": True, "message": "", "state": self.state()}

    def show_app(self, app_id: str) -> dict:
        """叫出窗口：已经在跑就再敲一次门，没跑就直接亮着启动。"""
        app = self._app(app_id)
        if not app.openable:
            return self._result(app, False, "这个软件没有窗口，它只在后台跑")
        return self.start(app_id, show=True)

    def _result(self, app, ok: bool, message: str) -> dict:
        return {
            "ok": ok,
            "message": message,
            "state": {
                "status": self.statuses([app]),
                "busy": sorted(self._busy),
                "installing": dict(self._installing),
                "installError": dict(self._install_error),
            },
        }

    # ---------------------------------------------------------------- 配置

    def open_config(self, app_id: str) -> dict:
        app = self._app(app_id)
        if not app.config_path.exists():
            return {"ok": False, "message": f"没有配置文件：{app.config_path.name}"}
        n.open_url(str(app.config_path))
        return {"ok": True, "message": ""}

    def open_folder(self, app_id: str) -> dict:
        app = self._app(app_id)
        if not app.path.is_dir():
            return {"ok": False, "message": f"文件夹不存在：{app.path}"}
        if not n.open_url(str(app.path)):
            return {"ok": False, "message": f"打不开文件夹：{app.path}"}
        return {"ok": True, "message": ""}

    def open_manager_config(self) -> None:
        n.open_url(str(self.cfg.path))

    def reload(self) -> dict:
        """重读配置：列表的项目和顺序都以 `apps` 为准。"""
        self.cfg = cfgmod.Config.load()
        self._stamp = cfgmod.stamp(self.cfg.path)
        self.apps = catalog.discover(self.cfg)
        self._by_id = {app.id: app for app in self.apps}
        self.hotkey.update(self._hotkey_specs())
        self.tray.refresh()
        return self.state()

    def reload_config(self) -> None:
        self.reload()
        self.ui.eval("window.location.reload()")

    # ---------------------------------------------------------------- 窗口

    def toggle_window(self) -> None:
        if self.ui.is_visible():
            self.ui.hide()
        else:
            self.show_window()

    def show_window(self) -> None:
        if self.quitting:
            return
        self.hidden = False  # Preserve a show request received before the window is ready.
        self.ui.show()

    def hide_window(self) -> None:
        self.ui.hide()

    def window_size(self) -> tuple[int, int]:
        return self.cfg.width, self.cfg.height
