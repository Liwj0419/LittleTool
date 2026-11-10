"""Win32 常量与函数绑定，业务逻辑不放在这里。

pywebview 自己算的窗口坐标在多屏 + 高 DPI 下会落到屏幕外面，标题栏也不能从
GUI 线程里改（会自己锁死自己），所以这几件事直接用 ctypes 调 user32。
非 Windows 平台上所有函数都安全地退化成"什么也不做"。
"""

from __future__ import annotations

import ctypes
import os
import sys
import time
from ctypes import wintypes

IS_WIN = sys.platform == "win32"

if IS_WIN:
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
else:  # pragma: no cover - 只是让 import 不炸
    user32 = None
    kernel32 = None

SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SWP_NOZORDER = 0x0004
SWP_NOACTIVATE = 0x0010
SWP_FRAMECHANGED = 0x0020
HWND_TOPMOST = -1
HWND_NOTOPMOST = -2

SW_RESTORE = 9
ASFW_ANY = 0xFFFFFFFF

MONITOR_DEFAULTTONEAREST = 2
SM_XVIRTUALSCREEN, SM_YVIRTUALSCREEN = 76, 77
SM_CXVIRTUALSCREEN, SM_CYVIRTUALSCREEN = 78, 79

if IS_WIN:
    user32.SetWindowPos.argtypes = [
        wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
        ctypes.c_int, ctypes.c_int, wintypes.UINT,
    ]
    user32.SetWindowPos.restype = wintypes.BOOL
    user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    user32.GetWindowRect.restype = wintypes.BOOL
    user32.SetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPCWSTR]
    user32.SetWindowTextW.restype = wintypes.BOOL
    user32.SetForegroundWindow.argtypes = [wintypes.HWND]
    user32.SetForegroundWindow.restype = wintypes.BOOL
    user32.GetForegroundWindow.restype = wintypes.HWND
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND,
                                                ctypes.POINTER(wintypes.DWORD)]
    user32.GetWindowThreadProcessId.restype = wintypes.DWORD
    user32.AttachThreadInput.argtypes = [wintypes.DWORD, wintypes.DWORD,
                                         wintypes.BOOL]
    user32.AttachThreadInput.restype = wintypes.BOOL
    user32.SetFocus.argtypes = [wintypes.HWND]
    user32.SetFocus.restype = wintypes.HWND
    user32.AllowSetForegroundWindow.argtypes = [ctypes.c_uint]
    user32.AllowSetForegroundWindow.restype = wintypes.BOOL
    kernel32.GetCurrentThreadId.restype = wintypes.DWORD


def allow_foreground() -> None:
    """把"可以抢前台"的权限让出去。

    双击/回车这一下，是**当前进程**收到了用户输入，Windows 只认它；
    已经开着的那个窗口自己去 SetForegroundWindow 是会被拒绝的。
    先松口（ASFW_ANY），对面那个窗口才能真正跳到最前、拿到键盘焦点。
    """
    if not IS_WIN:
        return
    try:
        user32.AllowSetForegroundWindow(ASFW_ANY)
    except Exception:
        pass


def find_hwnd(pid: int | None = None, wait: float = 3.0) -> int:
    """找属于本进程的顶层窗体窗口。

    不用窗口标题找：标题会随文件名变，而且崩溃残留的同名窗口会抢先命中。
    """
    if not IS_WIN:
        return 0

    pid = pid or os.getpid()
    end = time.time() + wait
    while True:
        hit: list[int] = []

        @ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
        def _cb(hwnd, _):
            p = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(p))
            if p.value != pid or not user32.IsWindowVisible(hwnd):
                return True
            cls = ctypes.create_unicode_buffer(256)
            user32.GetClassNameW(hwnd, cls, 256)
            if "WindowsForms" in cls.value and cls.value.count(".") >= 2:
                r = wintypes.RECT()
                user32.GetWindowRect(hwnd, ctypes.byref(r))
                if r.right - r.left > 200:      # 排除消息窗口
                    hit.append(hwnd)
            return True

        user32.EnumWindows(_cb, 0)
        if hit:
            return hit[0]
        if time.time() > end:
            return 0
        time.sleep(0.05)


def rect_of(hwnd: int) -> tuple[int, int, int, int]:
    """窗口矩形（物理像素）。"""
    if not IS_WIN or not hwnd:
        return 0, 0, 0, 0
    r = wintypes.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(r))
    return r.left, r.top, r.right - r.left, r.bottom - r.top


def set_title(hwnd: int, title: str) -> None:
    if IS_WIN and hwnd:
        try:
            user32.SetWindowTextW(hwnd, title)
        except Exception:
            pass


def restore(hwnd: int) -> None:
    """最小化过就先还原（pywebview 的 restore() 其实是"最大化/还原"来回切）。"""
    if not IS_WIN or not hwnd:
        return
    try:
        if user32.IsIconic(hwnd):
            user32.ShowWindow(hwnd, SW_RESTORE)
    except Exception:
        pass


def place(hwnd: int, saved=None, width: int = 1320, height: int = 860) -> bool:
    """把窗口摆进屏幕里。

    pywebview 自己算的坐标在多屏 + 高 DPI 下会落到屏幕外面
    （窗口明明开着，却什么都看不见，只有一片白），所以这里用 Win32 自己摆：
    有上次的位置就沿用，没有就在窗口所在显示器的工作区居中。
    """
    if not IS_WIN or not hwnd:
        return False

    class MONITORINFO(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.DWORD),
                    ("rcMonitor", wintypes.RECT),
                    ("rcWork", wintypes.RECT),
                    ("dwFlags", wintypes.DWORD)]

    _x, _y, w, h = rect_of(hwnd)
    w = w or width
    h = h or height

    hmon = user32.MonitorFromWindow(hwnd, MONITOR_DEFAULTTONEAREST)
    mi = MONITORINFO()
    mi.cbSize = ctypes.sizeof(MONITORINFO)
    user32.GetMonitorInfoW(hmon, ctypes.byref(mi))
    wa = mi.rcWork

    x = y = None
    if saved:
        try:
            sx, sy, sw, sh = [int(v) for v in saved]
            vx, vy = user32.GetSystemMetrics(SM_XVIRTUALSCREEN), \
                user32.GetSystemMetrics(SM_YVIRTUALSCREEN)
            vw, vh = user32.GetSystemMetrics(SM_CXVIRTUALSCREEN), \
                user32.GetSystemMetrics(SM_CYVIRTUALSCREEN)
            # 至少要有 120x80 露在虚拟屏幕里，否则当没存过
            if (sx + sw > vx + 120 and sx < vx + vw - 120
                    and sy + sh > vy + 80 and sy < vy + vh - 80
                    and sw >= 600 and sh >= 400):
                x, y, w, h = sx, sy, sw, sh
        except (TypeError, ValueError):
            pass

    if x is None:
        w = min(w, max(600, wa.right - wa.left - 40))
        h = min(h, max(400, wa.bottom - wa.top - 40))
        x = wa.left + max(0, (wa.right - wa.left - w) // 2)
        y = wa.top + max(0, (wa.bottom - wa.top - h) // 2)

    user32.SetWindowPos(hwnd, 0, int(x), int(y), int(w), int(h),
                        SWP_NOZORDER | SWP_NOACTIVATE)
    return True


def focus(hwnd: int) -> bool:
    """把窗口抢到前台，并把键盘焦点交给**网页内容**（不是窗体本身）。

    两个坑：

    * Windows 默认不允许后台进程抢前台（SetForegroundWindow 会被静默忽略），
      通用绕法是先 AttachThreadInput 挂到当前前台线程上再抢。
    * ``SetFocus`` 只能作用于**本线程**的窗口。热键/托盘回调跑在子线程上，
      直接 ``SetFocus(顶层窗体)`` 是空操作，而且就算生效，焦点也落在窗体上、
      不在 WebView2 的内容子窗口里 —— 页面收不到按键，Ctrl+F 自然"没反应"。
      所以这里挂到目标窗口的线程上，再把焦点交给 WebView2 的子窗口。
    """
    if not IS_WIN or not hwnd:
        return False
    try:
        if user32.IsIconic(hwnd):
            user32.ShowWindow(hwnd, SW_RESTORE)

        target = _web_child(hwnd) or hwnd
        tid_self = kernel32.GetCurrentThreadId()
        fg = user32.GetForegroundWindow()
        tid_fg = user32.GetWindowThreadProcessId(fg, None) if fg else 0
        tid_target = user32.GetWindowThreadProcessId(hwnd, None)

        attach = [t for t in {tid_fg, tid_target} if t and t != tid_self]
        for tid in attach:
            user32.AttachThreadInput(tid_self, tid, True)
        try:
            if fg != hwnd:
                user32.BringWindowToTop(hwnd)
                user32.SetForegroundWindow(hwnd)
            user32.SetFocus(target)
        finally:
            for tid in attach:
                user32.AttachThreadInput(tid_self, tid, False)

        if user32.GetForegroundWindow() != hwnd:
            # 还是不给面子：用"置顶一下再取消"顶到最前
            user32.SetWindowPos(hwnd, HWND_TOPMOST, 0, 0, 0, 0,
                                SWP_NOMOVE | SWP_NOSIZE)
            user32.SetWindowPos(hwnd, HWND_NOTOPMOST, 0, 0, 0, 0,
                                SWP_NOMOVE | SWP_NOSIZE)
            user32.SetForegroundWindow(hwnd)

        if user32.GetForegroundWindow() != hwnd:
            # 最后一招：敲一下 ALT 解除 Windows 的前台锁定，再抢一次
            # （只按 ALT 不带别的键，绝大多数程序不会有反应）
            user32.keybd_event(0x12, 0, 0, 0)
            user32.keybd_event(0x12, 0, 2, 0)
            user32.SetForegroundWindow(hwnd)

        # 前台可能变了：重新挂一次线程，确保 SetFocus 真的落到内容窗口上
        for tid in attach:
            user32.AttachThreadInput(tid_self, tid, True)
        try:
            user32.SetFocus(_web_child(hwnd) or hwnd)
        finally:
            for tid in attach:
                user32.AttachThreadInput(tid_self, tid, False)

        return user32.GetForegroundWindow() == hwnd
    except Exception:
        return False


def _web_child(hwnd: int) -> int:
    """找 WebView2 里真正吃键盘的那个子窗口。

    WinForms 的窗体 -> WebView2 控件 -> Chrome_WidgetWin_0 -> Chrome_WidgetWin_1，
    键盘消息由最深的那层处理。取不到就返回 0（调用方退回顶层窗口）。
    """
    if not IS_WIN or not hwnd:
        return 0

    best = (0, 0)          # (深度, hwnd)
    hits: list[int] = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
    def _cb(child, _):
        hits.append(child)
        return True

    try:
        user32.EnumChildWindows(hwnd, _cb, 0)
    except Exception:
        return 0

    for child in hits:
        try:
            if not user32.IsWindowVisible(child) or not user32.IsWindowEnabled(child):
                continue
            cls = ctypes.create_unicode_buffer(128)
            user32.GetClassNameW(child, cls, 128)
            if not cls.value.startswith("Chrome_WidgetWin"):
                continue
            r = wintypes.RECT()
            user32.GetWindowRect(child, ctypes.byref(r))
            if r.right - r.left < 100 or r.bottom - r.top < 100:
                continue
            depth, parent = 0, child
            while parent and parent != hwnd and depth < 16:
                parent = user32.GetParent(parent)
                depth += 1
            if depth >= best[0]:
                best = (depth, child)
        except Exception:
            continue
    return best[1]


def is_visible(hwnd: int) -> bool:
    if not IS_WIN or not hwnd:
        return False
    try:
        return bool(user32.IsWindowVisible(hwnd))
    except Exception:
        return False


def set_dpi_aware() -> None:
    """让窗口按物理像素算，避免高 DPI 下被系统再缩放一次（糊）。"""
    if not IS_WIN:
        return
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        try:
            user32.SetProcessDPIAware()
        except Exception:
            pass
