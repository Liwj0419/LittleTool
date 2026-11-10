"""Win32 常量与函数绑定，业务逻辑不放在这里。"""

from __future__ import annotations

import ctypes
from ctypes import wintypes

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

LRESULT = ctypes.c_ssize_t
WNDPROC = ctypes.WINFUNCTYPE(
    LRESULT, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM
)

# ---- 窗口消息 ----
WM_NCCALCSIZE = 0x0083
WM_NCHITTEST = 0x0084
WM_CLOSE = 0x0010
WM_NCLBUTTONDOWN = 0x00A1
WM_NCLBUTTONDBLCLK = 0x00A3
WM_APP_NATIVE = 0x8001  # 自定义：wParam 传 HT 码，在 UI 线程里发起原生拖动/缩放
WM_APP_FOCUS = 0x8002  # 自定义：在 UI 线程里抢前台，并把键盘焦点交给页面

# ---- 命中测试 ----
HTCLIENT = 1
HTCAPTION = 2
HTLEFT = 10
HTRIGHT = 11
HTTOP = 12
HTTOPLEFT = 13
HTTOPRIGHT = 14
HTBOTTOM = 15
HTBOTTOMLEFT = 16
HTBOTTOMRIGHT = 17

# ---- 激活 / 焦点 ----
SW_SHOW = 5
SW_RESTORE = 9
GW_CHILD = 5
VK_MENU = 0x12  # ALT
KEYEVENTF_KEYUP = 0x0002

# ---- 窗口样式 ----
GWL_STYLE = -16
GWLP_WNDPROC = -4
WS_THICKFRAME = 0x00040000

SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SWP_NOZORDER = 0x0004
SWP_NOACTIVATE = 0x0010
SWP_FRAMECHANGED = 0x0020
HWND_TOPMOST = -1
HWND_NOTOPMOST = -2

user32.SetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_void_p]
user32.SetWindowLongPtrW.restype = ctypes.c_void_p
user32.GetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int]
user32.GetWindowLongPtrW.restype = ctypes.c_ssize_t

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

user32.SendMessageW.argtypes = [
    wintypes.HWND,
    wintypes.UINT,
    wintypes.WPARAM,
    wintypes.LPARAM,
]
user32.SendMessageW.restype = LRESULT

user32.PostMessageW.argtypes = [
    wintypes.HWND,
    wintypes.UINT,
    wintypes.WPARAM,
    wintypes.LPARAM,
]
user32.PostMessageW.restype = wintypes.BOOL

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

SPI_GETWORKAREA = 0x0030
user32.SystemParametersInfoW.argtypes = [
    wintypes.UINT,
    wintypes.UINT,
    ctypes.c_void_p,
    wintypes.UINT,
]
user32.SystemParametersInfoW.restype = wintypes.BOOL


def work_area() -> tuple[int, int, int, int]:
    """主屏去掉任务栏之后的可视区域。"""
    r = wintypes.RECT()
    if user32.SystemParametersInfoW(SPI_GETWORKAREA, 0, ctypes.byref(r), 0):
        return r.left, r.top, r.right - r.left, r.bottom - r.top
    return 0, 0, 1920, 1080

user32.SetForegroundWindow.argtypes = [wintypes.HWND]
user32.SetForegroundWindow.restype = wintypes.BOOL
user32.GetForegroundWindow.restype = wintypes.HWND

user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
user32.ShowWindow.restype = wintypes.BOOL
user32.IsIconic.argtypes = [wintypes.HWND]
user32.IsIconic.restype = wintypes.BOOL
user32.BringWindowToTop.argtypes = [wintypes.HWND]
user32.BringWindowToTop.restype = wintypes.BOOL
user32.SetFocus.argtypes = [wintypes.HWND]
user32.SetFocus.restype = wintypes.HWND
user32.GetFocus.restype = wintypes.HWND
user32.GetWindow.argtypes = [wintypes.HWND, wintypes.UINT]
user32.GetWindow.restype = wintypes.HWND
user32.keybd_event.argtypes = [wintypes.BYTE, wintypes.BYTE, wintypes.DWORD, ctypes.c_size_t]
user32.keybd_event.restype = None

# SwitchToThisWindow 没写在文档里，但每个 Windows 都导出（任务栏点图标就是走它）。
# 后台进程被前台锁挡住时，它通常比 SetForegroundWindow 好使。
try:
    user32.SwitchToThisWindow.argtypes = [wintypes.HWND, wintypes.BOOL]
    user32.SwitchToThisWindow.restype = None
    _HAS_SWITCH = True
except AttributeError:
    _HAS_SWITCH = False


def switch_to_window(hwnd: int) -> None:
    """走「任务栏点图标」那条路把窗口激活。"""
    if not _HAS_SWITCH:
        return
    try:
        user32.SwitchToThisWindow(hwnd, True)
    except OSError:
        pass

user32.GetWindowThreadProcessId.argtypes = [
    wintypes.HWND,
    ctypes.POINTER(wintypes.DWORD),
]
user32.GetWindowThreadProcessId.restype = wintypes.DWORD
user32.AttachThreadInput.argtypes = [wintypes.DWORD, wintypes.DWORD, wintypes.BOOL]
user32.AttachThreadInput.restype = wintypes.BOOL
user32.ReleaseCapture.restype = wintypes.BOOL
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


def dpi_scale(hwnd: int | None = None) -> float:
    """窗口所在显示器的缩放比例（1.0 = 96 DPI）。"""
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
