"""Win32 能力：DPI、鼠标下的窗口、区域截图、窗口定位、剪贴板。

窗口/热键/托盘分别交给 pywebview / pynput / pystray，
这里只用标准库 ctypes 补那几个库没有的东西（见 AGENTS.md 第 1 条）。
"""

from __future__ import annotations

import ctypes
import os
import subprocess
import sys
from ctypes import wintypes

from . import config as cfgmod

IS_WIN = os.name == "nt"
IS_MAC = sys.platform == "darwin"

if IS_WIN:
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
    try:
        dwmapi = ctypes.WinDLL("dwmapi", use_last_error=True)
    except OSError:
        dwmapi = None

    # 64 位下句柄是 8 字节，不声明 argtypes/restype 会被截成 4 字节
    user32.WindowFromPoint.restype = wintypes.HWND
    user32.WindowFromPoint.argtypes = [wintypes.POINT]
    user32.GetAncestor.restype = wintypes.HWND
    user32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
    user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    user32.GetWindowRect.restype = wintypes.BOOL
    user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.GetClassNameW.restype = ctypes.c_int
    user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.GetWindowTextW.restype = ctypes.c_int
    user32.IsWindowVisible.argtypes = [wintypes.HWND]
    user32.IsWindowVisible.restype = wintypes.BOOL
    user32.SetWindowPos.argtypes = [
        wintypes.HWND, wintypes.HWND,
        ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
        wintypes.UINT,
    ]
    user32.SetWindowPos.restype = wintypes.BOOL
    user32.GetClientRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    user32.GetClientRect.restype = wintypes.BOOL
    user32.ClientToScreen.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.POINT)]
    user32.ClientToScreen.restype = wintypes.BOOL
    user32.GetForegroundWindow.restype = wintypes.HWND
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.c_void_p]
    user32.GetWindowThreadProcessId.restype = wintypes.DWORD
    user32.AttachThreadInput.argtypes = [wintypes.DWORD, wintypes.DWORD, wintypes.BOOL]
    user32.AttachThreadInput.restype = wintypes.BOOL
    user32.SetForegroundWindow.argtypes = [wintypes.HWND]
    user32.SetForegroundWindow.restype = wintypes.BOOL
    user32.BringWindowToTop.argtypes = [wintypes.HWND]
    user32.BringWindowToTop.restype = wintypes.BOOL
    user32.SetActiveWindow.argtypes = [wintypes.HWND]
    user32.SetActiveWindow.restype = wintypes.HWND
    user32.SetFocus.argtypes = [wintypes.HWND]
    user32.SetFocus.restype = wintypes.HWND

    kernel32.GlobalAlloc.restype = wintypes.HGLOBAL
    kernel32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
    kernel32.GlobalLock.restype = ctypes.c_void_p
    kernel32.GlobalLock.argtypes = [wintypes.HGLOBAL]
    kernel32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
    kernel32.GlobalFree.argtypes = [wintypes.HGLOBAL]
    user32.OpenClipboard.argtypes = [wintypes.HWND]
    user32.SetClipboardData.restype = wintypes.HANDLE
    user32.SetClipboardData.argtypes = [wintypes.UINT, wintypes.HANDLE]

    if dwmapi is not None:
        dwmapi.DwmGetWindowAttribute.argtypes = [
            wintypes.HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD
        ]
        dwmapi.DwmGetWindowAttribute.restype = ctypes.c_long
else:
    user32 = kernel32 = gdi32 = None
    dwmapi = None

# 这些窗口不是「一张图片」，命中就跳过
_SKIP_CLASSES = {
    "Progman",          # 桌面
    "WorkerW",          # 桌面壁纸层
    "Shell_TrayWnd",    # 任务栏
    "Shell_SecondaryTrayWnd",
    "Windows.UI.Core.CoreWindow",
}

SWP_NOACTIVATE = 0x0010
SWP_NOOWNERZORDER = 0x0200
HWND_TOPMOST = -1
GA_ROOT = 2
DWMWA_EXTENDED_FRAME_BOUNDS = 9


# --------------------------------------------------------------------------- DPI


def enable_dpi_awareness() -> None:
    """必须在建窗口之前调，否则坐标会被系统再缩放一次。"""
    if not IS_WIN:
        return
    try:
        ctypes.WinDLL("shcore").SetProcessDpiAwareness(2)  # per-monitor
        return
    except Exception:  # noqa: BLE001
        pass
    try:
        user32.SetProcessDPIAware()
    except Exception:  # noqa: BLE001
        pass


def dpi_scale() -> float:
    """1.0 = 100%，1.5 = 150%。"""
    if not IS_WIN:
        return 1.0
    try:
        dpi = user32.GetDpiForSystem()
        if dpi:
            return dpi / 96.0
    except Exception:  # noqa: BLE001
        pass
    try:
        hdc = user32.GetDC(0)
        try:
            return gdi32.GetDeviceCaps(hdc, 88) / 96.0
        finally:
            user32.ReleaseDC(0, hdc)
    except Exception:  # noqa: BLE001
        return 1.0


def virtual_screen() -> tuple[int, int, int, int]:
    """整个桌面（含多显示器）的物理像素矩形。"""
    if not IS_WIN:
        return (0, 0, 0, 0)
    return (
        user32.GetSystemMetrics(76),
        user32.GetSystemMetrics(77),
        user32.GetSystemMetrics(78),
        user32.GetSystemMetrics(79),
    )


# --------------------------------------------------------------------------- 窗口


def cursor_pos() -> tuple[int, int]:
    if not IS_WIN:
        return (0, 0)
    pt = wintypes.POINT()
    user32.GetCursorPos(ctypes.byref(pt))
    return (pt.x, pt.y)


def _class_name(hwnd) -> str:
    buf = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(hwnd, buf, 256)
    return buf.value


def _window_title(hwnd) -> str:
    buf = ctypes.create_unicode_buffer(512)
    try:
        user32.GetWindowTextW(hwnd, buf, 512)
    except Exception:  # noqa: BLE001
        return ""
    return buf.value


def _visual_rect(hwnd) -> tuple[int, int, int, int] | None:
    """优先用 DWM 的可见边界，比 GetWindowRect 少一圈透明拖拽边。"""
    if dwmapi is not None:
        r = wintypes.RECT()
        try:
            hr = dwmapi.DwmGetWindowAttribute(
                hwnd,
                DWMWA_EXTENDED_FRAME_BOUNDS,
                ctypes.byref(r),
                ctypes.sizeof(r),
            )
            if hr == 0 and r.right > r.left and r.bottom > r.top:
                return (r.left, r.top, r.right - r.left, r.bottom - r.top)
        except Exception:  # noqa: BLE001
            pass

    r = wintypes.RECT()
    if not user32.GetWindowRect(hwnd, ctypes.byref(r)):
        return None
    return (r.left, r.top, r.right - r.left, r.bottom - r.top)


def window_info_at_cursor(ignore_hwnd=None) -> dict | None:
    """鼠标指针下那个窗口的矩形（物理像素）。

    不是图片的（桌面、任务栏）返回 None；
    命中的是 ignore_hwnd（一般传覆盖层自己）也返回 None，免得自己锁自己。
    """
    if not IS_WIN:
        return None
    x, y = cursor_pos()
    hwnd = user32.WindowFromPoint(wintypes.POINT(x, y))
    if not hwnd:
        return None
    root = user32.GetAncestor(hwnd, GA_ROOT)
    if root:
        hwnd = root
    if ignore_hwnd and int(hwnd) == int(ignore_hwnd):
        return None
    cls = _class_name(hwnd)
    if cls in _SKIP_CLASSES:
        return None
    if not user32.IsWindowVisible(hwnd):
        return None

    rect = _visual_rect(hwnd)
    if rect is None:
        return None
    left, top, width, height = rect
    if width < 24 or height < 24:
        return None

    # 裁到桌面范围内，避免越界截出黑边
    vx, vy, vw, vh = virtual_screen()
    x0 = max(left, vx)
    y0 = max(top, vy)
    x1 = min(left + width, vx + vw)
    y1 = min(top + height, vy + vh)
    if x1 - x0 < 24 or y1 - y0 < 24:
        return None
    return {
        "rect": (x0, y0, x1 - x0, y1 - y0),
        "hwnd": hwnd,
        "class": cls,
        "title": _window_title(hwnd),
        "window_rect": (left, top, width, height),
    }


def window_rect_at_cursor(ignore_hwnd=None) -> tuple[int, int, int, int] | None:
    """只要矩形的快捷入口。"""
    info = window_info_at_cursor(ignore_hwnd)
    return info["rect"] if info else None


def set_window_rect(hwnd: int, rect: tuple[int, int, int, int]) -> None:
    """按物理像素摆窗口，绕开 pywebview 自带的 DPI 缩放。"""
    if not IS_WIN or not hwnd:
        return
    x, y, w, h = rect
    user32.SetWindowPos(
        hwnd,
        HWND_TOPMOST,
        int(x),
        int(y),
        int(w),
        int(h),
        SWP_NOACTIVATE | SWP_NOOWNERZORDER,
    )


def client_origin(hwnd: int) -> tuple[int, int, int, int] | None:
    """客户区在屏幕上的位置和大小——HTML 内容实际铺开的范围。"""
    if not IS_WIN or not hwnd:
        return None
    r = wintypes.RECT()
    if not user32.GetClientRect(hwnd, ctypes.byref(r)):
        return None
    pt = wintypes.POINT(0, 0)
    if not user32.ClientToScreen(hwnd, ctypes.byref(pt)):
        return None
    return (pt.x, pt.y, r.right - r.left, r.bottom - r.top)


def foreground_is(hwnd: int) -> bool:
    """覆盖层现在是不是前台窗口——键盘会不会真的落到页面上，就看这个。"""
    if not IS_WIN or not hwnd:
        return False
    try:
        return int(user32.GetForegroundWindow() or 0) == int(hwnd)
    except Exception:  # noqa: BLE001
        return False


def window_visible(hwnd: int) -> bool:
    """窗口此刻是不是还显示在屏幕上（异步收窗之后要等它真的消失）。"""
    if not IS_WIN or not hwnd:
        return False
    try:
        return bool(user32.IsWindowVisible(hwnd))
    except Exception:  # noqa: BLE001
        return False


def force_foreground(hwnd: int) -> bool:
    """抢前台：SetForegroundWindow 有前台锁，得先把相关线程的输入队列挂到当前线程上。

    只挂「当前前台线程」是不够的（以前就是这么写的）：待抢的窗口属于另一个线程
    （pywebview 的 WinForms UI 线程），它的输入队列没挂上，SetForegroundWindow
    照样被拒，而且失败是静默的——覆盖层看着置顶，前台其实还是底下那个窗口，
    键盘事件全被它吃掉，表现出来就是「盖住了但按 Esc 没反应」。
    """
    if not IS_WIN or not hwnd:
        return False
    cur = 0
    attached: list[int] = []
    try:
        if int(user32.GetForegroundWindow() or 0) == int(hwnd):
            return True
        cur = kernel32.GetCurrentThreadId()
        fg = user32.GetForegroundWindow()
        for tid in (
            user32.GetWindowThreadProcessId(fg, None) if fg else 0,
            user32.GetWindowThreadProcessId(hwnd, None),
        ):
            tid = int(tid or 0)
            if tid and tid != cur and tid not in attached:
                if user32.AttachThreadInput(tid, cur, True):
                    attached.append(tid)
        user32.BringWindowToTop(hwnd)
        user32.SetForegroundWindow(hwnd)
        user32.SetActiveWindow(hwnd)
        user32.SetFocus(hwnd)
    except Exception as e:  # noqa: BLE001
        cfgmod.log("[screen] 抢前台失败：%r" % (e,))
    finally:
        for tid in attached:
            try:
                user32.AttachThreadInput(tid, cur, False)
            except Exception:  # noqa: BLE001
                pass
    return foreground_is(hwnd)


# --------------------------------------------------------------------------- 截图


def grab(rect: tuple[int, int, int, int]):
    """截一块屏幕，返回 PIL.Image（RGB）。"""
    from PIL import ImageGrab

    x, y, w, h = rect
    bbox = (int(x), int(y), int(x + w), int(y + h))
    if IS_WIN:
        return ImageGrab.grab(bbox=bbox, all_screens=True).convert("RGB")
    return ImageGrab.grab(bbox=bbox).convert("RGB")


# --------------------------------------------------------------------------- 剪贴板


def set_clipboard(text: str) -> bool:
    if not text:
        return False
    try:
        if IS_WIN:
            return _win_set_clipboard(text)
        if IS_MAC:
            subprocess.run(["pbcopy"], input=text.encode("utf-8"), check=True)
            return True
        for cmd in (["xclip", "-selection", "clipboard"], ["xsel", "--clipboard", "--input"]):
            try:
                subprocess.run(cmd, input=text.encode("utf-8"), check=True)
                return True
            except FileNotFoundError:
                continue
    except Exception as e:  # noqa: BLE001
        cfgmod.log("[clipboard] 写入失败：%r" % (e,))
    return False


def _win_set_clipboard(text: str) -> bool:
    CF_UNICODETEXT = 13
    GMEM_MOVEABLE = 0x0002
    data = text.encode("utf-16-le") + b"\x00\x00"

    h = kernel32.GlobalAlloc(GMEM_MOVEABLE, len(data))
    if not h:
        return False
    p = kernel32.GlobalLock(h)
    if not p:
        kernel32.GlobalFree(h)
        return False
    ctypes.memmove(p, data, len(data))
    kernel32.GlobalUnlock(h)

    if not user32.OpenClipboard(0):
        kernel32.GlobalFree(h)
        return False
    try:
        user32.EmptyClipboard()
        if not user32.SetClipboardData(CF_UNICODETEXT, h):
            kernel32.GlobalFree(h)
            return False
        h = None  # 交给系统了
        return True
    finally:
        user32.CloseClipboard()
        if h:
            kernel32.GlobalFree(h)
