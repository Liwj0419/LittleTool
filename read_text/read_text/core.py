"""把本工具的热键、识别与覆盖层串起来。"""

from __future__ import annotations

import base64
import io
import os
import subprocess
import sys
import threading
import time

import webview

from . import config as cfgmod
from . import screen
from .hotkey import HotkeyManager
from .ocr import Ocr, OcrError
from .window import Overlay


def _to_data_url(image) -> str:
    """把截图变成页面能直接当背景铺的 data URL。"""
    buf = io.BytesIO()
    try:
        image.save(buf, "PNG", optimize=False)
        raw = buf.getvalue()
    finally:
        buf.close()
    return "data:image/png;base64," + base64.b64encode(raw).decode("ascii")


class App:
    def __init__(self, hidden: bool = False, debug: bool = False, selftest: bool = False) -> None:
        self.hidden = hidden
        self.debug = debug
        self.selftest = selftest
        self.quitting = False

        self.cfg = cfgmod.Config.load()
        self.overlay = Overlay(self.cfg)
        self.ocr = Ocr(self.cfg.get("ocr.upscale", 2))
        self.hotkey = HotkeyManager(self.on_hotkey)
        # 取消键的 Python 兜底：覆盖层的 renderer 卡死时页面里的 keydown 进不来，
        # 只能靠这里收窗。但它**不是全局快捷键**——下面 _on_cancel_hotkey 会先确认
        # 覆盖层此刻是前台窗口，否则在别的程序里按 Esc 也会把覆盖层关掉。
        self.cancel_hotkey = HotkeyManager(self._on_cancel_hotkey)
        self._busy = threading.Lock()
        self._busy_since = 0.0
        self._epoch = 0
        self._warned = False

        self.overlay.api.on_copy = self.on_copy
        self.overlay.api.on_cancel = self.on_cancel

    def _cancel_spec(self) -> str:
        return str(self.cfg.get("cancel", "Esc") or "Esc")

    def _on_cancel_hotkey(self) -> None:
        """取消键的兜底入口：只有覆盖层自己是前台窗口时才真的收窗。

        窗口内快捷键按约定只在焦点落在窗口上时成立（根目录 AGENTS.md §6.1），
        所以这里先校验前台窗口：焦点在别的程序上时按 Esc 什么也不做，
        用户要收窗就得先把焦点给覆盖层（点一下它）——那时候页面里的 keydown
        才是主路径；页面被卡死时才由这里接手。
        """
        if not self.overlay.visible:
            return
        hwnd = self.overlay.hwnd()
        if not hwnd or not screen.foreground_is(hwnd):
            return
        self.on_cancel("热键 %s（窗口在前台）" % self._cancel_spec())

    # ---------------------------------------------------------------- 启动

    def run(self) -> None:
        self.overlay.create()
        webview.start(self._boot, debug=self.debug)

    def _boot(self) -> None:
        self.hotkey.start(self.cfg.hotkey())
        self.cancel_hotkey.start(self._cancel_spec())
        threading.Thread(target=self._warm_up, name="warm", daemon=True).start()

    def _warm_up(self) -> None:
        """后台把 OCR 桥拉起来，别等到第一次按热键才启动。"""
        ok = self.ocr.start()
        if not ok:
            cfgmod.log("[app] 识别引擎不可用")

    # ---------------------------------------------------------------- 主流程

    def on_hotkey(self) -> None:
        if self.quitting:
            return
        if not self._busy.acquire(blocking=False):
            # 上一次还卡着。热键必须是最高优先级的逃生口：卡久了就直接收窗，
            # 不能让「覆盖层盖着屏幕 + 热键也失效」变成只能杀进程的局面。
            stuck = time.time() - self._busy_since
            if self.overlay.visible and stuck > 8.0:
                cfgmod.log("[app] 上一次取字已卡住 %.1fs，强制收起覆盖层" % stuck)
                self.on_cancel("热键接管")
            return
        self._busy_since = time.time()
        try:
            if self.overlay.visible:
                self.on_cancel("热键")
                return
            self._capture()
        except Exception as e:  # noqa: BLE001
            cfgmod.log("[app] 取字失败：%r" % (e,))
            if self.overlay.visible:
                self.overlay.message("取字失败：%s（按 Esc 退出）" % e, 0)
            else:
                self._notify("取字失败：%s" % e)
        finally:
            self._busy.release()

    def _capture(self) -> None:
        started = time.time()
        epoch = self._epoch
        self.overlay.hide()  # 截图前先把自己收起来
        self.overlay.wait_hidden()  # 上一次若是异步收的（Esc / 热键），等它真的消失再截
        # 覆盖层是置顶窗口，刚隐藏的瞬间还可能被命中，所以把它自己排除掉
        info = screen.window_info_at_cursor(ignore_hwnd=self.overlay.hwnd())
        if info is None:
            self._notify("鼠标下面没有可识别的窗口")
            return
        rect = info["rect"]

        # 先把画面截下来（此时覆盖层是隐藏的，截不到自己）
        image = screen.grab(rect)
        data_url = _to_data_url(image)

        # 立刻亮出来，让人看到「已经在干活」，而不是干等识别
        self.overlay.place(rect)
        self.overlay.show()
        self.overlay.wait_ready()
        self.overlay.set_background(data_url)
        self.overlay.message("正在识别这块 %d×%d 区域…" % (rect[2], rect[3]), 0)

        try:
            lines = self.ocr.recognize(image)
        except OcrError as e:
            cfgmod.log("[app] 识别失败：%s" % e)
            self.overlay.message("识别失败：%s（Esc 退出）" % e, 0)
            return
        finally:
            try:
                image.close()
            except Exception:  # noqa: BLE001
                pass

        if epoch != self._epoch:
            # 识别期间用户已经按 Esc 收窗了，别再把窗口内容灌回去
            cfgmod.log("[app] 识别完成时覆盖层已被取消，丢弃这次结果")
            return

        if not lines:
            self.overlay.message("这个区域里没识别到文字（Esc 退出）", 0)
            return

        count = self.overlay.render(lines, bg_size=(rect[2], rect[3]))
        diag = self.overlay.diag()
        cfgmod.log(
            "[app] 锁定 %dx%d @(%d,%d) 类=%s 标题=%r，%d 个文字块，%.0f ms，页面状态=%s"
            % (rect[2], rect[3], rect[0], rect[1], info["class"], info["title"],
               count, (time.time() - started) * 1000, diag or "{}")
        )

    # ---------------------------------------------------------------- 回调

    def on_copy(self, text: str) -> bool:
        if not text:
            return False
        if not screen.set_clipboard(text):
            cfgmod.log("[read_text] 剪贴板写入失败")
            self.overlay.message("复制失败，请再次按复制快捷键重试（Esc 退出）", 0)
            return False
        self.overlay.message("已复制 %d 个字符" % len(text))
        self.overlay.hide(wait=False)
        return True

    def on_cancel(self, source: str = "页面") -> None:
        self._epoch += 1
        if self.overlay.visible:
            cfgmod.log("[app] 收起覆盖层（%s）" % source)
        self.overlay.hide(wait=False)

    def _notify(self, text: str) -> None:
        cfgmod.log("[read_text] %s" % text)

    # ---------------------------------------------------------------- 本工具控制

    def show_window(self) -> None:
        """显式取字入口：鼠标在哪就识别哪个窗口。"""
        self.on_hotkey()

    def reload_config(self) -> None:
        self.cfg = cfgmod.Config.load()
        self.overlay.cfg = self.cfg
        self.ocr.upscale = max(1, min(3, int(self.cfg.get("ocr.upscale", 2) or 2)))
        self.hotkey.update(self.cfg.hotkey())
        self.cancel_hotkey.update(self._cancel_spec())

    def open_config(self) -> None:
        path = self.cfg.path
        try:
            if os.name == "nt":
                os.startfile(str(path))  # noqa: S606
            elif sys.platform == "darwin":
                subprocess.Popen(["open", str(path)])
            else:
                subprocess.Popen(["xdg-open", str(path)])
        except Exception as e:  # noqa: BLE001
            cfgmod.log("[app] 打开配置失败：%r" % (e,))

    def quit(self) -> None:
        if self.quitting:
            return
        self.quitting = True

        def watchdog():
            os._exit(0)

        timer = threading.Timer(2.5, watchdog)
        timer.daemon = True
        timer.start()

        self.hotkey.stop()
        self.cancel_hotkey.stop()
        self.ocr.stop()
        try:
            self.overlay.hide()
        except Exception:  # noqa: BLE001
            pass
        try:
            webview.windows[0].destroy()
        except Exception:  # noqa: BLE001
            pass
