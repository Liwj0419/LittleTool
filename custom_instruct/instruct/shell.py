"""无边框窗口的原生行为：收起（而不是关闭）、抢到前台、摆到屏幕中间。

pywebview 的窗口没有标题栏，Alt+F4 之类的关闭动作要走 WM_CLOSE，
这里把它改成「收起」，进程继续在托盘里待命；失去焦点（WM_ACTIVATE）
也顺手收起，跟以前点一下别处就消失的手感一致。
"""

from __future__ import annotations

import ctypes

from . import config as cfgmod
from . import native as n


class Shell:
    def __init__(self, hwnd: int, on_deactivate=None, on_close=None) -> None:
        self.hwnd = hwnd
        self.on_deactivate = on_deactivate  # 失去焦点时干什么（我们选择「收起」）
        self.on_close = on_close  # 收到 WM_CLOSE 时干什么
        self.was_active = False  # 这一次显示有没有真的拿到过焦点
        self._old_proc: int = 0
        self._proc = n.WNDPROC(self._wndproc) if n.IS_WINDOWS else None
        self._scale = n.dpi_scale(hwnd)
        self._attach()

    def reset_active(self) -> None:
        """每次显示前调用：已经在前台的就别当成「没拿到焦点」。"""
        if n.IS_WINDOWS and n.user32.GetForegroundWindow() == self.hwnd:
            self.was_active = True
        else:
            self.was_active = False

    # ---------------------------------------------------------------- 安装

    def _attach(self) -> None:
        if not n.IS_WINDOWS:
            return
        try:
            old = n.user32.SetWindowLongPtrW(
                self.hwnd, n.GWLP_WNDPROC, ctypes.cast(self._proc, ctypes.c_void_p)
            )
            self._old_proc = int(old or 0)
        except Exception as e:
            cfgmod.log(f"[shell] 接管窗口过程失败：{e!r}")

    def detach(self) -> None:
        if not n.IS_WINDOWS or not self._old_proc:
            return
        try:
            n.user32.SetWindowLongPtrW(
                self.hwnd, n.GWLP_WNDPROC, ctypes.c_void_p(self._old_proc)
            )
            self._old_proc = 0
        except Exception:
            pass

    # ---------------------------------------------------------------- 窗口过程

    def _wndproc(self, hwnd, msg, wparam, lparam):
        try:
            if msg == n.WM_ACTIVATE:
                if (int(wparam) & 0xFFFF) == n.WA_INACTIVE:
                    if self.on_deactivate is not None:
                        self.on_deactivate()
                else:
                    self.was_active = True
            elif msg == n.WM_CLOSE:
                if self.on_close is not None:
                    self.on_close()
                    return 0
        except Exception as e:
            cfgmod.log(f"[shell] 窗口消息处理失败：{e!r}")

        if self._old_proc:
            return n.user32.CallWindowProcW(self._old_proc, hwnd, msg, wparam, lparam)
        return n.user32.DefWindowProcW(hwnd, msg, wparam, lparam)

    # ---------------------------------------------------------------- 位置

    def window_rect(self) -> tuple[int, int, int, int]:
        if not n.IS_WINDOWS:
            return 0, 0, 0, 0
        r = n.wintypes.RECT()
        n.user32.GetWindowRect(self.hwnd, ctypes.byref(r))
        return r.left, r.top, r.right - r.left, r.bottom - r.top

    def set_bounds(self, x: int, y: int, w: int, h: int) -> None:
        if not n.IS_WINDOWS:
            return
        n.user32.SetWindowPos(
            self.hwnd, None, x, y, w, h, n.SWP_NOZORDER | n.SWP_NOACTIVATE
        )

    def place_center(self, width: int, height: int) -> None:
        """按逻辑尺寸（配置里写的宽高）摆到主屏中间。

        WinForms 自己按 DPI 换算出来的尺寸会有点出入，这里直接按物理像素算，
        配置写多少就是多少。
        """
        if not n.IS_WINDOWS:
            return
        scale = self._scale
        w = max(240, int(round(width * scale)))
        h = max(180, int(round(height * scale)))

        wx, wy, ww, wh = n.work_area()
        w = min(w, ww)
        h = min(h, wh)
        self.set_bounds(wx + (ww - w) // 2, wy + (wh - h) // 2, w, h)

    # ---------------------------------------------------------------- 前台

    def force_foreground(self, allow_alt: bool = True) -> bool:
        """从后台进程把窗口抢到最前面，成功返回 True。

        后台进程默认没资格抢前台：先把自己挂到当前前台线程的输入队列上，
        不行就换 SwitchToThisWindow，最后再模拟一次 Alt 敲击 —— 这样本进程
        就成了「最后一个输入事件的拥有者」，系统才会松开前台锁。
        """
        if not n.IS_WINDOWS:
            return True
        hwnd = self.hwnd
        if n.user32.GetForegroundWindow() == hwnd:
            return True

        fg = n.user32.GetForegroundWindow()
        tid_fg = n.user32.GetWindowThreadProcessId(fg, None) if fg else 0
        tid_win = n.user32.GetWindowThreadProcessId(hwnd, None)
        attached = False
        try:
            if tid_fg and tid_win and tid_fg != tid_win:
                attached = bool(n.user32.AttachThreadInput(tid_win, tid_fg, True))
            n.user32.BringWindowToTop(hwnd)
            n.user32.SetForegroundWindow(hwnd)
        finally:
            if attached:
                n.user32.AttachThreadInput(tid_win, tid_fg, False)

        if n.user32.GetForegroundWindow() == hwnd:
            return True

        n.user32.SwitchToThisWindow(hwnd, True)
        if n.user32.GetForegroundWindow() == hwnd:
            return True

        if not allow_alt:
            return False

        n.user32.keybd_event(n.VK_MENU, 0, 0, None)
        n.user32.keybd_event(n.VK_MENU, 0, n.KEYEVENTF_KEYUP, None)
        n.user32.SetForegroundWindow(hwnd)
        return n.user32.GetForegroundWindow() == hwnd
