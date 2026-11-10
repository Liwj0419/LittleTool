"""Win32 常量与函数绑定，业务逻辑不放在这里。

非 Windows 平台只留空壳：窗口行为退回 pywebview 自己的实现。
"""

from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes

IS_WINDOWS = sys.platform == "win32"

# ---- 窗口消息 ----
WM_ACTIVATE = 0x0006
WM_CLOSE = 0x0010
WA_INACTIVE = 0
WA_ACTIVE = 1
WA_CLICKACTIVE = 2

# ---- 窗口样式 ----
GWL_STYLE = -16
GCL_STYLE = -26
GWLP_WNDPROC = -4
CS_DROPSHADOW = 0x00020000

SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SWP_NOZORDER = 0x0004
SWP_NOACTIVATE = 0x0010
SWP_FRAMECHANGED = 0x0020
HWND_TOPMOST = -1
HWND_NOTOPMOST = -2

SPI_GETWORKAREA = 0x0030

VK_MENU = 0x12
KEYEVENTF_KEYUP = 0x0002

if IS_WINDOWS:
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    LRESULT = ctypes.c_ssize_t
    WNDPROC = ctypes.WINFUNCTYPE(
        LRESULT, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM
    )

    user32.SetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_void_p]
    user32.SetWindowLongPtrW.restype = ctypes.c_void_p
    user32.GetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.GetWindowLongPtrW.restype = ctypes.c_ssize_t

    user32.SetClassLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_void_p]
    user32.SetClassLongPtrW.restype = ctypes.c_void_p

    user32.CallWindowProcW.argtypes = [
        ctypes.c_void_p,
        wintypes.HWND,
        wintypes.UINT,
        wintypes.WPARAM,
        wintypes.LPARAM,
    ]
    user32.CallWindowProcW.restype = LRESULT

    user32.DefWindowProcW.argtypes = [
        wintypes.HWND,
        wintypes.UINT,
        wintypes.WPARAM,
        wintypes.LPARAM,
    ]
    user32.DefWindowProcW.restype = LRESULT

    user32.SetWindowPos.argtypes = [
        wintypes.HWND,
        wintypes.HWND,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        wintypes.UINT,
    ]
    user32.SetWindowPos.restype = wintypes.BOOL

    user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    user32.GetWindowRect.restype = wintypes.BOOL

    user32.SystemParametersInfoW.argtypes = [
        wintypes.UINT,
        wintypes.UINT,
        ctypes.c_void_p,
        wintypes.UINT,
    ]
    user32.SystemParametersInfoW.restype = wintypes.BOOL

    user32.SetForegroundWindow.argtypes = [wintypes.HWND]
    user32.SetForegroundWindow.restype = wintypes.BOOL
    user32.GetForegroundWindow.restype = wintypes.HWND
    user32.BringWindowToTop.argtypes = [wintypes.HWND]
    user32.BringWindowToTop.restype = wintypes.BOOL
    user32.SwitchToThisWindow.argtypes = [wintypes.HWND, wintypes.BOOL]
    user32.SwitchToThisWindow.restype = None
    user32.keybd_event.argtypes = [
        wintypes.BYTE,
        wintypes.BYTE,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.ULONG),
    ]
    user32.keybd_event.restype = None

    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user32.GetWindowThreadProcessId.restype = wintypes.DWORD
    user32.AttachThreadInput.argtypes = [wintypes.DWORD, wintypes.DWORD, wintypes.BOOL]
    user32.AttachThreadInput.restype = wintypes.BOOL

    user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
    user32.GetAsyncKeyState.restype = ctypes.c_short

    kernel32.GetCurrentThreadId.restype = wintypes.DWORD

    try:
        user32.GetDpiForWindow.argtypes = [wintypes.HWND]
        user32.GetDpiForWindow.restype = wintypes.UINT
        user32.GetDpiForSystem.restype = wintypes.UINT
        _HAS_DPI = True
    except AttributeError:  # Windows 10 1607 之前
        _HAS_DPI = False
else:  # pragma: no cover - 只为让模块在别的系统上也能 import
    user32 = None
    kernel32 = None
    LRESULT = ctypes.c_ssize_t
    WNDPROC = None
    _HAS_DPI = False


def work_area() -> tuple[int, int, int, int]:
    """主屏去掉任务栏之后的可视区域。"""
    if not IS_WINDOWS:
        return 0, 0, 1920, 1080
    r = wintypes.RECT()
    if user32.SystemParametersInfoW(SPI_GETWORKAREA, 0, ctypes.byref(r), 0):
        return r.left, r.top, r.right - r.left, r.bottom - r.top
    return 0, 0, 1920, 1080


def dpi_scale(hwnd: int | None = None) -> float:
    """窗口所在显示器的缩放比例（1.0 = 96 DPI）。"""
    if not IS_WINDOWS:
        return 1.0
    if _HAS_DPI:
        try:
            dpi = user32.GetDpiForWindow(hwnd) if hwnd else user32.GetDpiForSystem()
            if dpi:
                return dpi / 96.0
        except OSError:
            pass
    try:
        dc = user32.GetDC(0)
        if dc:
            dpi = ctypes.WinDLL("gdi32").GetDeviceCaps(dc, 88)  # LOGPIXELSX
            user32.ReleaseDC(0, dc)
            if dpi:
                return dpi / 96.0
    except OSError:
        pass
    return 1.0
