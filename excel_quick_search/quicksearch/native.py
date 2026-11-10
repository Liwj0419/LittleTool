"""Win32 常量与函数绑定：抢前台、居中、深色标题栏。

业务逻辑不放在这里。非 Windows 平台下所有函数都安全退化成空操作，
所以上层不需要到处写 sys.platform 判断。
"""

from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes

IS_WIN = sys.platform == "win32"

SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SWP_NOZORDER = 0x0004
SWP_NOACTIVATE = 0x0010
HWND_TOPMOST = -1
HWND_NOTOPMOST = -2
SPI_GETWORKAREA = 0x0030

VK_MENU = 0x12
KEYEVENTF_KEYUP = 0x0002

DWMWA_USE_IMMERSIVE_DARK_MODE = 20
DWMWA_USE_IMMERSIVE_DARK_MODE_OLD = 19

if IS_WIN:
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    try:
        dwmapi = ctypes.WinDLL("dwmapi", use_last_error=True)
    except OSError:  # 理论上不会发生，兜个底
        dwmapi = None

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

    user32.GetClientRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    user32.GetClientRect.restype = wintypes.BOOL
    user32.ClientToScreen.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.POINT)]
    user32.ClientToScreen.restype = wintypes.BOOL
    user32.IsWindowVisible.argtypes = [wintypes.HWND]
    user32.IsWindowVisible.restype = wintypes.BOOL

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

    user32.GetWindowThreadProcessId.argtypes = [
        wintypes.HWND,
        ctypes.POINTER(wintypes.DWORD),
    ]
    user32.GetWindowThreadProcessId.restype = wintypes.DWORD
    user32.AttachThreadInput.argtypes = [wintypes.DWORD, wintypes.DWORD, wintypes.BOOL]
    user32.AttachThreadInput.restype = wintypes.BOOL
    user32.BringWindowToTop.argtypes = [wintypes.HWND]
    user32.BringWindowToTop.restype = wintypes.BOOL
    user32.SetFocus.argtypes = [wintypes.HWND]
    user32.SetFocus.restype = wintypes.HWND
    user32.keybd_event.argtypes = [
        wintypes.BYTE,
        wintypes.BYTE,
        wintypes.DWORD,
        ctypes.c_size_t,
    ]
    user32.keybd_event.restype = None
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
else:
    user32 = kernel32 = dwmapi = None
    _HAS_DPI = False


def dpi_scale(hwnd: int | None = None) -> float:
    """窗口所在显示器的缩放比例（1.0 = 96 DPI）。"""
    if not IS_WIN or not _HAS_DPI:
        return 1.0
    try:
        dpi = user32.GetDpiForWindow(hwnd) if hwnd else user32.GetDpiForSystem()
        if dpi:
            return dpi / 96.0
    except OSError:
        pass
    return 1.0


def work_area() -> tuple[int, int, int, int]:
    """主屏去掉任务栏之后的可视区域。"""
    if not IS_WIN:
        return 0, 0, 1280, 800
    r = wintypes.RECT()
    if user32.SystemParametersInfoW(SPI_GETWORKAREA, 0, ctypes.byref(r), 0):
        return r.left, r.top, r.right - r.left, r.bottom - r.top
    return 0, 0, 1280, 800


def window_rect(hwnd: int) -> tuple[int, int, int, int]:
    if not IS_WIN or not hwnd:
        return 0, 0, 0, 0
    r = wintypes.RECT()
    if user32.GetWindowRect(hwnd, ctypes.byref(r)):
        return r.left, r.top, r.right - r.left, r.bottom - r.top
    return 0, 0, 0, 0


def client_rect(hwnd: int) -> tuple[int, int, int, int]:
    """客户区（不含标题栏和边框）在屏幕上的位置，截图时用。"""
    if not IS_WIN or not hwnd:
        return 0, 0, 0, 0
    r = wintypes.RECT()
    if not user32.GetClientRect(hwnd, ctypes.byref(r)):
        return window_rect(hwnd)
    origin = wintypes.POINT(0, 0)
    if not user32.ClientToScreen(hwnd, ctypes.byref(origin)):
        return window_rect(hwnd)
    return origin.x, origin.y, r.right - r.left, r.bottom - r.top


def is_visible(hwnd: int) -> bool:
    if not IS_WIN or not hwnd:
        return False
    return bool(user32.IsWindowVisible(hwnd))


def center_origin(width: int, height: int) -> tuple[int, int]:
    """算出让窗口居中的左上角坐标。

    返回值是**逻辑像素**：pywebview 的 create_window(x=, y=) 收逻辑坐标，
    自己会按显示器缩放换成物理像素。
    """
    scale = dpi_scale(None) or 1.0
    wx, wy, ww, wh = work_area()
    x = wx + (ww - width * scale) / 2
    y = wy + (wh - height * scale) / 2
    return int(round(x / scale)), int(round(y / scale))


def set_dark_titlebar(hwnd: int, dark: bool) -> None:
    """Win10 1809+ 的标题栏深色模式；不支持的版本静默忽略。"""
    if not IS_WIN or not hwnd or dwmapi is None:
        return
    value = ctypes.c_int(1 if dark else 0)
    for attr in (DWMWA_USE_IMMERSIVE_DARK_MODE, DWMWA_USE_IMMERSIVE_DARK_MODE_OLD):
        try:
            if dwmapi.DwmSetWindowAttribute(
                wintypes.HWND(hwnd), ctypes.c_uint(attr),
                ctypes.byref(value), ctypes.sizeof(value),
            ) == 0:
                return
        except OSError:
            continue


def force_foreground(hwnd: int) -> None:
    """从后台进程把窗口抢到最前面。

    三步走，前一步不成就换下一步：
    1. 把窗口线程和前台线程的输入队列临时接在一起，再 SetForegroundWindow；
    2. 还是不行（全屏游戏、锁屏程序占着前台锁），先模拟一次 Alt 键松开前台锁；
    3. 最后兜底：置顶再取消置顶，至少让它浮在最上面。
    """
    if not IS_WIN or not hwnd:
        return
    if user32.GetForegroundWindow() == hwnd:
        return

    fg = user32.GetForegroundWindow()
    tid_fg = user32.GetWindowThreadProcessId(fg, None) if fg else 0
    tid_win = user32.GetWindowThreadProcessId(hwnd, None)
    attached = False
    try:
        if tid_fg and tid_win and tid_fg != tid_win:
            attached = bool(user32.AttachThreadInput(tid_win, tid_fg, True))
        user32.BringWindowToTop(hwnd)
        user32.SetForegroundWindow(hwnd)
        user32.SetFocus(hwnd)
    except OSError:
        pass
    finally:
        if attached:
            try:
                user32.AttachThreadInput(tid_win, tid_fg, False)
            except OSError:
                pass

    if user32.GetForegroundWindow() == hwnd:
        return

    # 前台锁：Windows 认为「用户刚刚的输入给了别的窗口」，就不许我们抢。
    # 模拟一次 Alt 按键，系统会把前台锁放开一格，这是各家窗口工具的老办法。
    try:
        user32.keybd_event(VK_MENU, 0, 0, 0)
        user32.keybd_event(VK_MENU, 0, KEYEVENTF_KEYUP, 0)
        user32.SetForegroundWindow(hwnd)
    except OSError:
        pass

    if user32.GetForegroundWindow() == hwnd:
        return

    try:
        user32.SetWindowPos(hwnd, HWND_TOPMOST, 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE)
        user32.SetWindowPos(hwnd, HWND_NOTOPMOST, 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE)
        user32.SetForegroundWindow(hwnd)
    except OSError:
        pass


def system_theme() -> str:
    """系统当前是深色还是浅色（用于 theme = auto）。"""
    if not IS_WIN:
        return "dark"
    try:
        import winreg

        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize",
        ) as key:
            value, _ = winreg.QueryValueEx(key, "AppsUseLightTheme")
            return "light" if value else "dark"
    except OSError:
        return "dark"


def allow_foreground() -> None:
    """用户操作后允许目标查看器获取前台。"""
    if IS_WIN:
        user32.AllowSetForegroundWindow.argtypes = [wintypes.DWORD]
        user32.AllowSetForegroundWindow.restype = wintypes.BOOL
        user32.AllowSetForegroundWindow(0xFFFFFFFF)
