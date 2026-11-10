"""pywebview 覆盖层窗口：无边框、置顶，尺寸贴住目标窗口。

不用窗口透明：WebView2 在「url 加载 + 隐藏后再显示」时透明不稳定，
首次出现会先铺一层底色。改成把目标区域的截图当背景铺进页面，
用户看到的就是原图像素，文字层只是浮在上面。
"""

from __future__ import annotations

import json
import queue
import threading
import time

import webview

from . import config as cfgmod
from . import screen

# 一次 evaluate_js 最多等这么久（秒）。pywebview 内部是「阻塞 Invoke + 信号量
# 无超时等待」，页面忙 / 窗口状态异常时调用线程会**永久**卡住，所以统一包一层超时。
EVAL_TIMEOUT = 3.0

# 背景图分块灌入的段长（字符）。一次塞几 MB 的脚本会让 WebView2 的 renderer
# 长时间不响应，这段时间里页面的 keydown / mousedown 只能排队，看起来就是卡死。
BG_CHUNK = 256 * 1024


class _Api:
    """页面调回 Python 的入口（pywebview 会把这些方法挂到 pywebview.api 上）。"""

    def __init__(self) -> None:
        self.on_copy = None
        self.on_cancel = None

    def copy(self, text: str) -> bool:
        cb = self.on_copy
        if callable(cb):
            return bool(cb(text or ""))
        return False

    def cancel(self) -> bool:
        cb = self.on_cancel
        if callable(cb):
            cb()
        return True


class Overlay:
    def __init__(self, cfg: cfgmod.Config) -> None:
        self.cfg = cfg
        self.api = _Api()
        self.window = None
        self._visible = False
        self._lock = threading.Lock()

    # ---------------------------------------------------------------- 创建

    def create(self):
        self.window = webview.create_window(
            cfgmod.APP_TITLE,
            url=str(cfgmod.assets_dir() / "index.html"),
            width=320,
            height=200,
            x=0,
            y=0,
            frameless=True,
            on_top=True,
            shadow=False,
            resizable=False,
            min_size=(1, 1),   # 默认是 (200,100)，会把小贴图撑大导致背景拉伸
            easy_drag=False,
            hidden=True,
            focus=False,
            text_select=True,
            zoomable=False,
            js_api=self.api,
        )
        return self.window

    @property
    def visible(self) -> bool:
        return self._visible

    def hwnd(self) -> int:
        try:
            return int(self.window.native.Handle.ToInt64())
        except Exception:  # noqa: BLE001
            return 0

    # ---------------------------------------------------------------- 显示

    def place(self, rect) -> bool:
        """按物理像素把窗口摆到目标位置（绕开 pywebview 的 DPI 缩放）。"""
        hwnd = self.hwnd()
        if not hwnd:
            cfgmod.log("[overlay] 拿不到窗口句柄")
            return False
        screen.set_window_rect(hwnd, rect)
        return True

    def show(self) -> None:
        # 不持锁调窗口：window.show() 会阻塞等 UI 线程，持锁时别人（Esc 兜底）拿不到锁就跟着卡。
        # 标记先翻上去：show 本身也要等 UI 线程，这期间按 Esc 必须能命中。
        self._visible = True
        try:
            self.window.show()
        except Exception as e:  # noqa: BLE001
            cfgmod.log("[overlay] show 失败：%r" % (e,))
            self._visible = False
            return
        self._claim_focus()

    def _claim_focus(self) -> bool:
        """置顶不等于拿到前台。抢不到时效地记一笔，否则「盖住了却按什么都没反应」完全没线索。"""
        hwnd = self.hwnd()
        for _ in range(3):
            if screen.force_foreground(hwnd):
                return True
            time.sleep(0.05)
        cfgmod.log("[overlay] 抢不到前台焦点，键盘不会到页面（Esc 已由全局钩子兜底）")
        return False

    # ---------------------------------------------------------------- evaluate_js

    def _eval(self, js: str, timeout: float = EVAL_TIMEOUT):
        """带超时的 evaluate_js。

        超时只是「这次不管了」，卡住的工作线程是 daemon，进程退出时会一起走，
        所以一次卡顿不会把热键线程和 _busy 一起拖死——这点比以前重要得多。
        """
        box: queue.Queue = queue.Queue(maxsize=1)

        def work() -> None:
            try:
                box.put((True, self.window.evaluate_js(js)))
            except Exception as e:  # noqa: BLE001
                box.put((False, e))

        threading.Thread(target=work, name="eval", daemon=True).start()
        try:
            ok, value = box.get(timeout=timeout)
        except queue.Empty:
            cfgmod.log("[overlay] evaluate_js 超时（%.1fs）：%s" % (timeout, js[:48]))
            return None
        if not ok:
            cfgmod.log("[overlay] evaluate_js 失败：%r" % (value,))
            return None
        return value

    def wait_ready(self, timeout: float = 1.5) -> bool:
        """等页面脚本就绪。隐藏期间 evaluate_js 可能不返回，所以要在 show 之后调。"""
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self._eval("typeof window.__message === 'function'", 0.4):
                return True
            time.sleep(0.04)
        cfgmod.log("[overlay] 页面就绪超时")
        return False

    def hide(self, wait: bool = True) -> None:
        """wait=False 给「Esc / 复制」这条路径用：先把标记翻掉，收窗放到后台做。

        window.hide() 是阻塞式 Invoke，页面或 UI 线程正忙时它会一直等；
        放在按键回调里就等于「按了 Esc 却回不来」。
        """
        with self._lock:
            if not self._visible:
                return
            self._visible = False
        if wait:
            self._do_hide()
        else:
            threading.Thread(target=self._do_hide, name="hide", daemon=True).start()

    def _do_hide(self) -> None:
        try:
            self.window.hide()
        except Exception as e:  # noqa: BLE001
            cfgmod.log("[overlay] hide 失败：%r" % (e,))

    def wait_hidden(self, timeout: float = 0.6) -> bool:
        """等窗口真的从屏幕上消失。

        `hide(wait=False)` 是异步的（Esc / 热键那条路），紧接着再按热键截图时，
        窗口可能还盖在屏幕上——那样就会把自己截进去，越锁越大。
        """
        hwnd = self.hwnd()
        if not hwnd:
            return True
        deadline = time.time() + timeout
        while time.time() < deadline:
            if not screen.window_visible(hwnd):
                return True
            time.sleep(0.02)
        cfgmod.log("[overlay] 收窗超时，窗口可能还盖在屏幕上")
        return False

    # ---------------------------------------------------------------- 内容

    def set_background(self, data_url: str) -> bool:
        """分块灌 data URL：单条几 MB 的脚本会让 renderer 长时间不响应。"""
        if not data_url:
            return False
        if not self._eval("window.__bgBegin()", 1.0):
            return False
        total = len(data_url)
        for i in range(0, total, BG_CHUNK):
            part = data_url[i:i + BG_CHUNK]
            if self._eval("window.__bgPush(%s)" % json.dumps(part), 2.0) is None:
                return False
        return self._eval("window.__bgEnd()", 2.0) is not None

    def render(self, lines: list[list[dict]], bg_size: tuple[int, int] | None = None) -> int:
        # bgW/bgH 是截图的**物理像素**尺寸：页面据此换算缩放比，
        # 不再依赖「背景图是否已经解码完」（naturalWidth 为 0 时会算错，字块全部错位、点不中）
        payload = {
            "lines": lines,
            "cfg": self.cfg.page(),
            "bgW": int(bg_size[0]) if bg_size else 0,
            "bgH": int(bg_size[1]) if bg_size else 0,
        }
        js = "window.__render(%s)" % json.dumps(payload, ensure_ascii=True)
        value = self._eval(js, 4.0)
        try:
            return int(value or 0)
        except (TypeError, ValueError):
            return 0

    def message(self, text: str, ms: int = 0) -> None:
        """ms=0 表示常驻，直到被下一条提示替换或窗口关闭。"""
        js = "window.__message(%s, %d)" % (json.dumps(text, ensure_ascii=True), int(ms))
        self._eval(js, 2.0)

    def diag(self) -> dict:
        """页面自报状态，写进日志：下次再出问题能一眼看出是焦点、缩放还是图没解码。"""
        raw = self._eval("window.__diag && window.__diag()", 2.0)
        if not raw:
            return {}
        try:
            return json.loads(raw) if isinstance(raw, str) else dict(raw)
        except Exception:  # noqa: BLE001
            return {}
