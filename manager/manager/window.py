"""pywebview 窗口：本地静态服务 + JS 桥 + 无边框拖动/缩放。

界面就是 assets/index.html 一个文件，没有框架、没有构建步骤。
所有状态都由页面主动来拿（get_state / poll），后端不往页面里塞脚本，
省得两边时序互相等。
"""

from __future__ import annotations

import functools
import os
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import webview

from . import config as cfgmod
from . import native as n
from .shell import Frameless

# 纯列表界面用不到硬件加速；--no-sandbox 是因为部分安全软件会拦 WebView2 子进程的
# 沙箱，导致 GPU / 渲染进程反复崩。崩了之后 evaluate_js 会一直等下去，界面就僵住。
os.environ.setdefault("WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS", "--no-sandbox --disable-gpu")


class _Handler(SimpleHTTPRequestHandler):
    """assets 目录的静态文件服务（界面只有一个 index.html 和一个图标）。"""

    def end_headers(self) -> None:
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def log_message(self, *args) -> None:  # 别往控制台刷请求日志
        pass


class _Server:
    """把 assets 目录挂到 127.0.0.1 的临时端口上。"""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.httpd: ThreadingHTTPServer | None = None

    def start(self) -> str:
        handler = functools.partial(_Handler, directory=str(self.root))
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        threading.Thread(target=self.httpd.serve_forever, name="http", daemon=True).start()
        port = self.httpd.server_address[1]
        return f"http://127.0.0.1:{port}"

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

    # ---------------------------------------------------------------- 状态
    def get_state(self) -> dict:
        return self._ui.core.state()

    def poll(self) -> dict:
        return self._ui.core.poll()

    def reload(self) -> dict:
        return self._ui.core.reload()

    # ---------------------------------------------------------------- 开关
    def toggle(self, app_id: str, on: bool) -> dict:
        return self._ui.core.toggle(str(app_id), bool(on))

    def show_app(self, app_id: str) -> dict:
        return self._ui.core.show_app(str(app_id))

    def start_all(self) -> dict:
        return self._ui.core.start_all()

    def stop_all(self) -> dict:
        return self._ui.core.stop_all()

    # ---------------------------------------------------------------- 配置
    def open_config(self, app_id: str) -> dict:
        return self._ui.core.open_config(str(app_id))

    def open_folder(self, app_id: str) -> dict:
        return self._ui.core.open_folder(str(app_id))

    def open_manager_config(self) -> bool:
        self._ui.core.open_manager_config()
        return True

    # ---------------------------------------------------------------- 窗口
    def hide(self) -> bool:
        self._ui.hide()
        return True

    def quit(self) -> bool:
        self._ui.core.quit()
        return True

    def drag(self) -> bool:
        self._ui.begin_native(n.HTCAPTION)
        return True

    def minimize(self) -> bool:
        if self._ui.shell is not None:
            self._ui.shell.minimize()
        return True


class Ui:
    def __init__(self, core) -> None:
        self.core = core
        self.window: webview.Window | None = None
        self.shell: Frameless | None = None
        self._server: _Server | None = None
        self._ready = False
        self._visible = False
        self._gui_tid: int | None = None

    # ---------------------------------------------------------------- 启动

    def start(self) -> None:
        assets = cfgmod.assets_dir()
        cfg = self.core.cfg

        self._server = _Server(assets)
        try:
            base = self._server.start()
        except Exception as e:
            cfgmod.log(f"[http] 本地服务启动失败，退回 file://：{e!r}")
            base = ""
        url = f"{base}/index.html" if base else (assets / "index.html").as_uri()

        self.window = webview.create_window(
            "Manager",
            url=url,
            js_api=_Api(self),
            width=max(cfg.min_width, cfg.width),
            height=max(cfg.min_height, cfg.height),
            min_size=(cfg.min_width, cfg.min_height),
            frameless=True,
            easy_drag=False,
            resizable=True,
            text_select=True,
            background_color="#101216",
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
        """GUI 起来之后：接管无边框行为、摆到屏幕中间，然后按需露一次面。"""
        try:
            self._gui_tid = threading.get_ident()
            win = self.window
            win.events.loaded.wait(15)

            hwnd = int(win.native.Handle.ToInt64())
            cfg = self.core.cfg
            # 点关闭 / Alt+F4 = 收进托盘，进程继续待命
            self.shell = Frameless(
                hwnd,
                on_close=self._on_close,
                min_size=(cfg.min_width, cfg.min_height),
            )
            self.shell.place_center(cfg.width, cfg.height)

            if not self.core.hidden:
                self.show()
        except Exception as e:
            cfgmod.log(f"[boot] {e!r}")

    # ---------------------------------------------------------------- 显示

    def is_visible(self) -> bool:
        return self._visible

    def show(self) -> None:
        win = self.window
        if win is None:
            return
        self._visible = True
        try:
            win.restore()
            win.show()
        except Exception as e:
            cfgmod.log(f"[show] {e!r}")
            return

        if self.shell is not None:
            threading.Thread(
                target=self.shell.force_foreground, name="front", daemon=True
            ).start()

        self.eval("window.__refresh&&window.__refresh();window.__focus&&window.__focus();")

    def hide(self) -> None:
        self._visible = False
        if self.window is not None:
            try:
                self.window.hide()
            except Exception as e:
                cfgmod.log(f"[hide] {e!r}")

    def _on_close(self) -> None:
        """窗口过程里收到 WM_CLOSE：交给短命线程处理，别和消息循环打架。"""
        if self.core.cfg.close_to_tray:
            threading.Thread(target=self.hide, name="hide", daemon=True).start()
        else:
            threading.Thread(target=self.core.quit, name="quit", daemon=True).start()

    def quit(self) -> None:
        """结束 GUI 消息循环（pywebview 的 destroy 在无边框窗口上会留僵尸进程）。"""
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

    def begin_native(self, hit: int) -> None:
        if self.shell is not None:
            self.shell.post_begin(hit)

    def eval(self, script: str):
        if not self._ready or self.window is None:
            return None
        if self._gui_tid is not None and threading.get_ident() == self._gui_tid:
            threading.Thread(
                target=self._eval_raw, args=(script,), daemon=True
            ).start()
            return None
        return self._eval_raw(script)

    def _eval_raw(self, script: str):
        try:
            return self.window.evaluate_js(script)
        except Exception as e:
            cfgmod.log(f"[js] {e!r}")
            return None

    # ---------------------------------------------------------------- 事件

    def _on_loaded(self) -> None:
        self._gui_tid = threading.get_ident()
        self._ready = True
        self.eval("window.__ready&&window.__ready();")
