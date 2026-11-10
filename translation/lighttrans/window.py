"""pywebview 窗口：本地静态服务 + JS 桥 + 无边框拖动/缩放。"""

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
from .shell import HT_EDGE, Frameless

# 纯文字界面用不到硬件加速；--no-sandbox 是因为部分安全软件会拦 WebView2 子进程的
# 沙箱，导致 GPU / 渲染进程反复崩。崩了之后 evaluate_js 会一直等下去，整个界面就僵住。
# pywebview 不开放这个参数，但 WebView2 认这个环境变量（会追加到它自己的参数后面）。
os.environ["WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS"] = "--no-sandbox --disable-gpu"


class _Handler(SimpleHTTPRequestHandler):
    def end_headers(self) -> None:
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def log_message(self, *args) -> None:  # 别往控制台刷请求日志
        pass


class _Server:
    """把 assets 目录挂到 127.0.0.1 的临时端口上。

    比 file:// 省心：页面有正常来源，fetch 跨域和 navigator.clipboard 都按
    普通网页的规矩来（127.0.0.1 在 Chromium 里算安全上下文）。
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

    def get_config(self) -> dict:
        return self._ui.core.cfg.page()

    def hide(self) -> None:
        self._ui.core.hide_window()

    def minimize(self) -> None:
        self._ui.minimize()

    def quit(self) -> None:
        self._ui.core.quit()

    def open_config(self) -> None:
        self._ui.core.open_config()

    def start_drag(self) -> None:
        self._ui.begin_native(n.HTCAPTION)

    def start_resize(self, edge: str) -> None:
        self._ui.begin_native(HT_EDGE.get(str(edge), n.HTCLIENT))


class Ui:
    def __init__(self, core) -> None:
        self.core = core
        self.window: webview.Window | None = None
        self.shell: Frameless | None = None
        self._server: _Server | None = None
        self._ready = False  # 页面加载完、可以 evaluate_js 了
        self._booted = False
        self._painted = threading.Event()  # WebView2 首帧已经画过
        self._warmup = False  # 正在做「静默预热」，别和 show 抢窗口状态
        self._save_timer: threading.Timer | None = None

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

        self.window = webview.create_window(
            "翻译",
            url=url,
            js_api=_Api(self),
            width=max(320, cfg.width),
            height=max(320, cfg.height),
            min_size=(320, 320),
            frameless=True,
            easy_drag=False,
            resizable=True,
            text_select=True,
            background_color="#0f1114",
            hidden=True,
        )

        win = self.window
        win.events.loaded += self._on_loaded
        win.events.resized += self._on_geometry
        win.events.moved += self._on_geometry

        icon = assets / "icon.ico"
        webview.start(
            self._boot,
            debug=self.core.debug,
            icon=str(icon) if icon.exists() else None,
        )

    def _boot(self) -> None:
        """GUI 起来之后：接管无边框行为、摆到上次的位置。"""
        try:
            win = self.window
            win.events.loaded.wait(15)

            hwnd = int(win.native.Handle.ToInt64())
            # 关窗口（Alt+F4 之类）= 收起来，进程继续待命
            self.shell = Frameless(
                hwnd, on_close=self._hide_soon, on_focus=self._focus_page
            )

            saved = cfgmod.load_bounds()
            if saved:
                self.shell.set_bounds(*saved)
            else:
                cfg = self.core.cfg
                self.shell.place_center(cfg.width, cfg.height)

            self._booted = True
            if not self.core.hidden:
                self.show(reset=False)
            else:
                self._warmup_hidden()
        except Exception as e:
            cfgmod.log(f"[boot] {e!r}")

    def _warmup_hidden(self) -> None:
        """hidden 启动时让 WebView2 把首帧画完，否则第一次唤出只有一片白。

        pywebview 的 hidden=True 只是把窗口 Show 一下立刻 Hide（中间还把
        Opacity 设成 0）。Chromium 认为这个窗口从来没真正可见过，页面虽然
        加载完成（CDP 里 URL 和 title 都正常），却不提交任何画面；等后面
        show() 把窗体拉出来，看到的就是窗体默认的白底，RedrawWindow 和
        改尺寸都救不回来，感觉就像卡死了。

        这里用 pywebview 自己的 show/hide 把窗口短暂显示一帧，逼它提交
        首帧，再收回待命。必须走 pywebview 的接口（内部会 Invoke 到 UI
        线程）；直接碰 WinForms 控件或跨线程 ShowWindow 会和接管过的窗口
        过程抢 GIL，把 UI 线程锁死。
        """
        win = self.window
        if win is None:
            return
        self._warmup = True
        try:
            win.show()
            time.sleep(0.5)
            if self.core.hidden:  # 预热期间用户已经唤出，就别再藏回去了
                win.hide()
        except Exception as e:
            cfgmod.log(f"[warmup] {e!r}")
        finally:
            self._painted.set()
            self._warmup = False

    # ---------------------------------------------------------------- 显示

    def show(self, reset: bool = True) -> None:
        if self._warmup:
            self._painted.wait(2.0)  # 预热没结束就别动窗口，免得被 hide 盖掉
        win = self.window
        if win is None:
            return
        try:
            win.restore()
            win.show()
        except Exception as e:
            cfgmod.log(f"[show] {e!r}")
            return

        # 抢前台 + 把键盘焦点交给页面（交给 UI 线程做，见 Frameless._grab）
        if self.shell is not None:
            self.shell.force_foreground()

        if reset and self._ready:
            self.eval("window.__bridgeReset&&window.__bridgeReset();")

        self._focus_retry()

    def _focus_page(self) -> None:
        """窗口已经被激活之后，把光标放回输入框。

        页面里的 focus() 在窗口没到前台时会被浏览器直接忽略（document.hasFocus()
        还是 false），所以这一枪必须等 SetFocus 之后再打，放在 show() 里不够。
        """
        self.eval("window.__bridgeFocus&&window.__bridgeFocus();")

    def _focus_retry(self) -> None:
        """隔一拍再补一次：窗口刚 Show 出来时，系统偶尔会把前台又还回去。"""

        def tick() -> None:
            shell = self.shell
            if shell is None:
                return
            if shell.is_foreground():
                self._focus_page()  # 已经在前台，补一次页面焦点就行
            else:
                shell.force_foreground()

        timer = threading.Timer(0.25, tick)
        timer.daemon = True
        timer.start()

    def hide(self) -> None:
        if self.window is not None:
            try:
                self.window.hide()
            except Exception as e:
                cfgmod.log(f"[hide] {e!r}")

    def minimize(self) -> None:
        """最小化到任务栏。再唤起时 show() 里的 restore() 会把它还原成正常窗口。"""
        if self.window is not None:
            try:
                self.window.minimize()
            except Exception as e:
                cfgmod.log(f"[minimize] {e!r}")

    def _hide_soon(self) -> None:
        # 在窗口过程里直接隐藏容易和自己打架，丢给一个短命线程
        threading.Thread(target=self.hide, name="hide", daemon=True).start()

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
            import clr  # noqa: F401  pythonnet 由 pywebview 的 winforms 后端带进来

            from System import Action
            from System.Windows.Forms import Application

            form = win.native
            form.BeginInvoke(Action(Application.ExitThread))
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
        """在页面里跑一段 JS。

        **绝对不能在 UI 线程里同步调用**：pywebview 的 evaluate_js 会 Invoke 到
        消息循环上执行，然后 semaphore.acquire() 等 JS 回调；而这个回调又是排回
        UI 线程的（sync context）。JS 慢一点点没赶在 acquire 之前完成，UI 线程就
        把自己锁死，窗口直接卡住不再响应。
        """
        if not self._ready or self.window is None:
            return None
        if self._on_gui_thread():
            threading.Thread(
                target=self._eval_raw, args=(script,), daemon=True
            ).start()
            return None
        return self._eval_raw(script)

    def _on_gui_thread(self) -> bool:
        """当前线程是不是窗口的消息循环线程。

        不能拿事件回调里记的线程 id 来比：pywebview 的 Event 是把 handler 丢到
        临时线程里跑的（webview/event.py 里 threading.Thread(target=execute)），
        所以 _on_loaded 跑在哪个线程完全看运气。直接问「这个窗口属于哪个线程」
        最可靠——而且 UI 线程 id 和 Python 的 get_ident() 不是一回事，必须都用
        Win32 的线程 id 比较。
        """
        if self.shell is None:
            return False
        tid = n.user32.GetWindowThreadProcessId(self.shell.hwnd, None)
        return bool(tid) and tid == n.kernel32.GetCurrentThreadId()

    def _eval_raw(self, script: str):
        try:
            return self.window.evaluate_js(script)
        except Exception as e:
            cfgmod.log(f"[js] {e!r}")
            return None

    def send_config(self) -> None:
        payload = json.dumps(self.core.cfg.page(), ensure_ascii=False)
        self.eval(f"window.__bridgeApplyConfig({payload});")

    def begin_native(self, hit: int) -> None:
        if self.shell is not None:
            self.shell.post_begin(hit)

    # ---------------------------------------------------------------- 事件

    def _on_loaded(self) -> None:
        self._ready = True
        self.send_config()


    def _on_geometry(self, *_args) -> None:
        if not self._booted:
            return
        if self._save_timer is not None:
            self._save_timer.cancel()
        self._save_timer = threading.Timer(0.6, self._save_bounds)
        self._save_timer.daemon = True
        self._save_timer.start()

    def _save_bounds(self) -> None:
        if self.shell is None:
            return
        try:
            cfgmod.save_bounds(self.shell.window_rect())
        except Exception as e:
            cfgmod.log(f"[bounds] {e!r}")
