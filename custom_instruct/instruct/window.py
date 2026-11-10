"""pywebview 窗口：本地静态服务 + JS 桥 + 无边框窗口行为。"""

from __future__ import annotations

import functools
import json
import os
import threading
import time
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import webview

from . import config as cfgmod
from .shell import Shell

# 界面用不到硬件加速；--no-sandbox 是因为部分安全软件会拦 WebView2 子进程的沙箱，
# 导致 GPU / 渲染进程反复崩。崩了之后 evaluate_js 会一直等下去，界面看起来就是卡死。
# pywebview 不开放这个参数，但 WebView2 认这个环境变量（会追加到它自己的参数后面）。
os.environ["WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS"] = "--no-sandbox --disable-gpu"

# 窗口最小尺寸，配置写得太小也不会变成一个点
MIN_WIDTH = 320
MIN_HEIGHT = 220


class _Handler(SimpleHTTPRequestHandler):
    def end_headers(self) -> None:
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def log_message(self, *args) -> None:  # 别往控制台刷请求日志
        pass


class _Server:
    """把 assets 目录挂到 127.0.0.1 的临时端口上。

    比 file:// 省心：页面有正常来源，剪贴板之类都按普通网页的规矩来。
    """

    def __init__(self, root: Path) -> None:
        self.root = root
        self.httpd: ThreadingHTTPServer | None = None

    def start(self) -> str:
        handler = functools.partial(_Handler, directory=str(self.root))
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        threading.Thread(
            target=self.httpd.serve_forever, name="http", daemon=True
        ).start()
        port = self.httpd.server_address[1]
        return f"http://127.0.0.1:{port}/index.html"

    def stop(self) -> None:
        httpd, self.httpd = self.httpd, None
        if httpd is not None:
            try:
                httpd.shutdown()
                httpd.server_close()
            except Exception:
                pass


class _Api:
    """页面里的 window.pywebview.api.* 就是这些方法。"""

    def __init__(self, ui: "Ui") -> None:
        self._ui = ui

    def get_state(self) -> dict:
        return self._ui.state()

    def execute(self, entry_id=None, query: str = "", moved: bool = False) -> dict:
        return self._ui.core.execute(entry_id, query, bool(moved))

    def reload(self) -> dict:
        return self._ui.core.reload_config()

    def hide(self) -> None:
        self._ui.hide()

    def minimize(self) -> None:
        self._ui.minimize()

    def quit(self) -> None:
        self._ui.core.quit()

    def open_config(self) -> None:
        self._ui.core.open_config()


class Ui:
    def __init__(self, core) -> None:
        self.core = core
        self.window: webview.Window | None = None
        self.shell: Shell | None = None
        self._server: _Server | None = None
        self._ready = False  # 页面加载完、可以 evaluate_js 了
        self._visible = False
        self._shown_at = 0.0
        self._gui_tid: int | None = None

    # ---------------------------------------------------------------- 启动

    def start(self) -> None:
        assets = cfgmod.assets_dir()
        cfg = self.core.cfg

        self._server = _Server(assets)
        try:
            url = self._server.start()
        except Exception as e:
            cfgmod.log(f"[http] 本地服务启动失败，退回 file://：{e!r}")
            url = (assets / "index.html").as_uri()

        width, height = self.core.window_size()
        self.window = webview.create_window(
            "Custom Instruct",
            url=url,
            js_api=_Api(self),
            width=width,
            height=height,
            frameless=True,
            easy_drag=False,
            resizable=False,
            on_top=cfg.flag("always_on_top"),
            background_color="#14161a",
            hidden=True,
        )

        self.window.events.loaded += self._on_loaded

        icon = assets / "icon.ico"
        webview.start(
            self._boot,
            debug=self.core.debug,
            icon=str(icon) if icon.exists() else None,
        )

    def _boot(self) -> None:
        """GUI 起来之后：接管无边框行为，然后按需露一次面。"""
        try:
            win = self.window
            win.events.loaded.wait(15)

            hwnd = int(win.native.Handle.ToInt64())
            self.shell = Shell(
                hwnd, on_deactivate=self._on_deactivate, on_close=self._hide_soon
            )

            if self.core.show_at_start:
                self.show(reset=True)
        except Exception as e:
            cfgmod.log(f"[boot] {e!r}")

    # ---------------------------------------------------------------- 显示

    def show(self, reset: bool = True) -> None:
        win = self.window
        if win is None:
            return

        self._visible = True
        self._shown_at = time.monotonic()
        if self.shell is not None:
            self.shell.reset_active()

        # 摆位置、Show、抢前台这一串必须跑在 UI 线程里，
        # 抢前台要靠 AttachThreadInput 挂到前台线程的输入队列上，跑在别的线程上不生效
        self._invoke(self._show_on_gui)

        self.send_state()
        if reset:
            self.eval("window.__bridgeReset&&window.__bridgeReset();")

    def _show_on_gui(self) -> None:
        win = self.window
        if win is None:
            return

        width, height = self.core.window_size()
        if self.shell is not None:
            self.shell.place_center(width, height)

        try:
            win.restore()
            win.show()
        except Exception as e:
            cfgmod.log(f"[show] {e!r}")
            self._visible = False
            return

        cfgmod.log(f"[show] shell={'ok' if self.shell else 'none'}")
        self._grab_foreground(0)

    def _invoke(self, fn) -> None:
        """把函数丢到 UI 线程执行（拿不到 native 就直接调）。"""
        form = getattr(self.window, "native", None)
        if form is None:
            fn()
            return
        try:
            from System import Action

            if form.InvokeRequired:
                form.Invoke(Action(fn))
                return
        except Exception as e:
            cfgmod.log(f"[invoke] {e!r}")
        fn()

    def _grab_foreground(self, attempt: int) -> None:
        """刚 Show() 出来的窗口经常抢不到焦点，隔一会儿再试几次。

        前几次只用温和手段，实在抢不到才模拟 Alt 敲击（那个动静大一点）。
        """
        if not self._visible or self.shell is None:
            return
        if self.shell.force_foreground(allow_alt=attempt >= 2) or attempt >= 8:
            return
        timer = threading.Timer(0.03, self._grab_foreground, args=(attempt + 1,))
        timer.daemon = True
        timer.start()

    def hide(self) -> None:
        self._visible = False
        cfgmod.log("[hide]")
        if self.window is not None:
            try:
                self.window.hide()
            except Exception as e:
                cfgmod.log(f"[hide] {e!r}")

    def minimize(self) -> None:
        """最小化到任务栏。再唤起时 _show_on_gui() 里的 restore() 会还原成正常窗口。"""
        if self.window is not None:
            try:
                self.window.minimize()
            except Exception as e:
                cfgmod.log(f"[minimize] {e!r}")

    def _hide_soon(self) -> None:
        # 在窗口过程里直接隐藏容易和自己打架，丢给一个短命线程
        threading.Thread(target=self.hide, name="hide", daemon=True).start()

    def _on_deactivate(self) -> None:
        """点到别的地方就把启动器收起来（以前 WinForms 的 Deactivate 行为）。"""
        if not self._visible:
            return
        if not self.core.cfg.flag("hide_on_blur"):
            return
        # 从没真正拿到过焦点的话，这次失活只是「抢前台失败」的副产物，不能因此把窗口藏了
        if self.shell is not None and not self.shell.was_active:
            return
        # 刚显示的那一下系统会先发一次失活，别理它
        if time.monotonic() - self._shown_at < 0.35:
            return
        cfgmod.log("[deactivate] 收起")
        self._hide_soon()

    def quit(self) -> None:
        """结束 GUI 消息循环。

        pywebview 的 window.destroy() 走的是 Form.Close()，它内部那条
        「最后一个窗口关掉就 Application.Exit()」的路径在无边框窗口上不会真正
        结束 Application.Run()，实测会留下一个没有窗口的僵尸进程，
        所以这里直接把消息循环退掉。
        """
        win = self.window
        if win is None:
            return
        try:
            from System import Action
            from System.Windows.Forms import Application

            win.native.BeginInvoke(Action(Application.ExitThread))
        except Exception as e:
            cfgmod.log(f"[quit] {e!r}")
            os._exit(0)

    def stop(self) -> None:
        if self._server is not None:
            self._server.stop()
        if self.shell is not None:
            self.shell.detach()

    # ---------------------------------------------------------------- JS

    def eval(self, script: str):
        """在页面里跑一段 JS。从 UI 线程调用时改成异步，否则会和消息循环互等。"""
        if not self._ready or self.window is None:
            return None
        if self._gui_tid is not None and threading.get_ident() == self._gui_tid:
            threading.Thread(target=self._eval_raw, args=(script,), daemon=True).start()
            return None
        return self._eval_raw(script)

    def _eval_raw(self, script: str):
        try:
            return self.window.evaluate_js(script)
        except Exception as e:
            cfgmod.log(f"[js] {e!r}")
            return None

    def state(self) -> dict:
        store = self.core.store
        cfg = self.core.cfg
        return {
            "entries": [entry.page() for entry in store.entries],
            "warnings": list(store.errors),
            "path": str(store.path),
            "config": str(cfg.path),
            "theme": cfg.theme,
            "shortcuts": cfg.shortcuts,
            "behavior": cfg.behavior,
        }

    def send_state(self) -> None:
        payload = json.dumps(self.state(), ensure_ascii=False)
        self.eval(f"window.__bridgeState({payload});")

    # ---------------------------------------------------------------- 事件

    def _on_loaded(self) -> None:
        self._gui_tid = threading.get_ident()
        self._ready = True
        self.send_state()
