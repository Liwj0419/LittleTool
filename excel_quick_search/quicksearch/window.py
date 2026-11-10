"""pywebview 窗口：本地静态服务 + JS 桥 + 收起/退出。"""

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
from . import native as n

# 纯文字界面用不到硬件加速；--no-sandbox 是因为部分安全软件会拦 WebView2 子进程的
# 沙箱，导致 GPU / 渲染进程反复崩。崩了之后 evaluate_js 会一直等下去，界面就僵住了。
# pywebview 不开放这个参数，但 WebView2 认这个环境变量（会追加到它自己的参数后面）。
os.environ.setdefault(
    "WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS", "--no-sandbox --disable-gpu"
)


class _Handler(SimpleHTTPRequestHandler):
    def end_headers(self) -> None:
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def log_message(self, *args) -> None:  # 别往控制台刷请求日志
        pass


class _Server:
    """把 assets 目录挂到 127.0.0.1 的临时端口上。

    比 file:// 省心：页面有正常来源，navigator.clipboard 按普通网页的规矩来
    （127.0.0.1 在 Chromium 里算安全上下文）。
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

    # ---------------------------------------------------------------- 基础

    def get_config(self) -> dict:
        return self._ui.core.cfg.page()

    def hide(self) -> None:
        self._ui.core.hide_window()

    def quit(self) -> None:
        self._ui.core.quit()

    def open_config(self) -> None:
        self._ui.core.open_config()

    def reload_config(self) -> None:
        self._ui.core.reload_config()

    # ---------------------------------------------------------------- 数据

    def scan(self, key: str) -> dict:
        return self._ui.core.scan_source(str(key))

    def rescan(self) -> dict:
        return self._ui.core.rescan()

    def open_file(self, path: str) -> dict:
        return self._ui.core.open_file(str(path))

    def reveal(self, path: str) -> bool:
        return self._ui.core.reveal(str(path))


class Ui:
    def __init__(self, core) -> None:
        self.core = core
        self.window: webview.Window | None = None
        self._server: _Server | None = None
        self._ready = False  # 页面加载完、可以 evaluate_js 了
        self._stale = True  # 页面里还留着上次的输入，下次亮出来之前得清掉
        self._gui_tid: int | None = None
        self._hwnd_cache = 0

    # ---------------------------------------------------------------- 启动

    def start(self) -> None:
        assets = cfgmod.assets_dir()
        cfg = self.core.cfg
        x, y = n.center_origin(cfg.width, cfg.height)

        self._server = _Server(assets)
        try:
            url = self._server.start()
        except Exception as e:
            cfgmod.log(f"[http] 本地服务启动失败，退回 file://：{e!r}")
            url = (assets / "index.html").as_uri()

        self.window = webview.create_window(
            "Excel 快速搜索",
            url=url,
            js_api=_Api(self),
            width=cfg.width,
            height=cfg.height,
            x=x,
            y=y,
            min_size=(560, 380),
            resizable=True,
            on_top=bool(cfg.window_on_top()),
            text_select=False,
            background_color="#0f1114",
            hidden=True,
        )

        self.window.events.loaded += self._on_loaded
        self.window.events.closing += self._on_closing

        icon = assets / "icon.ico"
        webview.start(
            self._boot,
            debug=self.core.debug,
            icon=str(icon) if icon.exists() else None,
        )

    def _boot(self) -> None:
        """GUI 起来之后：套上深色标题栏，需要的话直接亮出来。

        位置和尺寸在 create_window 时就按逻辑像素给 pywebview 了，
        它自己会按显示器缩放换算成物理像素，不用在这里再 SetWindowPos ——
        从外面改窗口大小，WinForms 不一定把 WebView2 子控件跟着重排，
        会在底部留一条没被页面盖住的缝。
        """
        try:
            hwnd = self._wait_hwnd()
            if hwnd:
                n.set_dark_titlebar(hwnd, self.core.theme() == "dark")
            if not self.core.hidden:
                self.show(reset=False)
        except Exception as e:
            cfgmod.log(f"[boot] {e!r}")

    def _wait_hwnd(self, timeout: float = 6.0) -> int:
        end = time.time() + timeout
        while time.time() < end:
            hwnd = self.hwnd()
            if hwnd:
                return hwnd
            time.sleep(0.05)
        return 0

    def hwnd(self) -> int:
        if self._hwnd_cache:
            return self._hwnd_cache
        try:
            handle = int(self.window.native.Handle.ToInt64())
            if handle:
                self._hwnd_cache = handle
            return handle
        except Exception:
            return 0

    # ---------------------------------------------------------------- 显示

    def show(self, reset: bool = True) -> None:
        win = self.window
        if win is None:
            return
        try:
            win.restore()
            win.show()
        except Exception as e:
            cfgmod.log(f"[show] {e!r}")
            return

        hwnd = self.hwnd()
        if hwnd:
            self._activate(hwnd)

        if reset and self._stale:
            self.reset_page()

    def _activate(self, hwnd: int) -> None:
        """抢前台交给窗口自己的线程去做 —— 从别的工作线程调 SetForegroundWindow，
        系统多半不认（尤其是前台被全屏程序占着的时候）。"""
        try:
            from System import Action

            self.window.native.BeginInvoke(Action(lambda: n.force_foreground(hwnd)))
            return
        except Exception as e:
            cfgmod.log(f"[activate] 走 GUI 线程失败，直接抢：{e!r}")
        n.force_foreground(hwnd)

    def hide(self) -> None:
        if self.window is not None:
            try:
                self.window.hide()
            except Exception as e:
                cfgmod.log(f"[hide] {e!r}")
        # 收起就重置：下次唤起是全新的空白输入，不带上一次的搜索词
        if not self.core.quitting:
            self.reset_page()

    def reset_page(self) -> None:
        """清空搜索框、丢掉扫描结果、回到选数据源。

        在窗口已经隐藏之后执行，用户看不见这一步；这样下次亮出来就是干净的，
        也不会出现「先闪一下上次的文字、再被 JS 清掉」的那种跳动。
        """
        if not self._ready:
            return
        # 末尾回个 1：拿不到说明这段 JS 没跑成，`_stale` 保持 True，下次唤起再补一次
        if self.eval("(window.__bridgeReset&&window.__bridgeReset(),1)") == 1:
            self._stale = False

    def _on_closing(self):
        """点标题栏的叉 / Alt+F4：收进托盘，进程继续待命。"""
        if self.core.quitting:
            return True
        threading.Thread(target=self.hide, name="hide", daemon=True).start()
        return False

    def quit(self) -> None:
        """结束 GUI 消息循环。

        pywebview 的 window.destroy() 走的是 Form.Close()，它内部那条
        「最后一个窗口关掉就 Application.Exit()」的路径在隐藏窗口上不会真正
        结束 Application.Run()，实测会留下一个没有窗口的僵尸进程，
        所以这里直接把消息循环退掉。
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

    def stop(self) -> None:
        if self._server is not None:
            self._server.stop()

    # ---------------------------------------------------------------- JS

    def eval(self, script: str):
        """在页面里跑一段 JS。从 UI 线程调用时改成异步，否则会和消息循环互等。"""
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

    def send_config(self) -> None:
        payload = json.dumps(self.core.cfg.page(), ensure_ascii=False)
        self.eval(f"window.__bridgeApplyConfig({payload});")

    def notify_theme(self) -> None:
        dark = self.core.theme() == "dark"
        hwnd = self.hwnd()
        if hwnd:
            n.set_dark_titlebar(hwnd, dark)

    # ---------------------------------------------------------------- 事件

    def _on_loaded(self) -> None:
        self._gui_tid = threading.get_ident()
        self._ready = True
        self._stale = False  # 页面加载时自己会 resetAll 一次
        self.send_config()
