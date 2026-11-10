"""无边框窗口的原生行为：四边缩放、顶栏拖动、抢到前台、读写物理位置。

pywebview 的无边框窗口本身不带缩放边框，这里给窗口补上 WS_THICKFRAME，
并把 WM_NCCALCSIZE 的边框扣掉（客户区仍然铺满整个窗口），
拖动 / 缩放则用「PostMessage 一个自定义消息 → UI 线程里发 WM_NCLBUTTONDOWN」
的方式交给系统自己的移动 / 缩放循环，比用 JS 一点点挪窗口顺滑。
"""

from __future__ import annotations

import ctypes

from . import config as cfgmod
from . import native as n

HT_EDGE = {
    "l": n.HTLEFT,
    "r": n.HTRIGHT,
    "t": n.HTTOP,
    "b": n.HTBOTTOM,
    "tl": n.HTTOPLEFT,
    "tr": n.HTTOPRIGHT,
    "bl": n.HTBOTTOMLEFT,
    "br": n.HTBOTTOMRIGHT,
}


class Frameless:
    def __init__(self, hwnd: int, on_close=None, on_focus=None):
        self.hwnd = hwnd
        self.on_close = on_close  # 收到 WM_CLOSE 时干什么（我们选择「收起」）
        self.on_focus = on_focus  # 抢到前台之后干什么（把光标放回输入框）
        self._old_proc: int = 0
        self._proc = n.WNDPROC(self._wndproc)  # 必须留引用，否则回调会被回收
        self._scale = n.dpi_scale(hwnd)
        self._attach()

    # ---------------------------------------------------------------- 安装

    def _attach(self) -> None:
        try:
            style = n.user32.GetWindowLongPtrW(self.hwnd, n.GWL_STYLE)
            if not style & n.WS_THICKFRAME:
                n.user32.SetWindowLongPtrW(
                    self.hwnd, n.GWL_STYLE, style | n.WS_THICKFRAME
                )

            old = n.user32.SetWindowLongPtrW(
                self.hwnd, n.GWLP_WNDPROC, ctypes.cast(self._proc, ctypes.c_void_p)
            )
            self._old_proc = int(old or 0)

            # 让系统按新样式重算一遍边框，顺便走一次我们的 WM_NCCALCSIZE
            n.user32.SetWindowPos(
                self.hwnd,
                None,
                0,
                0,
                0,
                0,
                n.SWP_FRAMECHANGED | n.SWP_NOMOVE | n.SWP_NOSIZE | n.SWP_NOZORDER,
            )
        except Exception as e:
            cfgmod.log(f"[frameless] 接管失败：{e!r}")

    def detach(self) -> None:
        try:
            if self._old_proc:
                n.user32.SetWindowLongPtrW(
                    self.hwnd, n.GWLP_WNDPROC, ctypes.c_void_p(self._old_proc)
                )
                self._old_proc = 0
        except Exception:
            pass

    # ---------------------------------------------------------------- 窗口过程

    def _wndproc(self, hwnd, msg, wparam, lparam):
        try:
            if msg == n.WM_NCCALCSIZE:
                if wparam:
                    return 0  # 客户区 = 整个窗口，原生边框一点不留
            elif msg == n.WM_NCHITTEST:
                return self._hit_test(lparam)
            elif msg == n.WM_NCLBUTTONDBLCLK:
                return 0  # 双击顶栏不要最大化
            elif msg == n.WM_CLOSE:
                # Alt+F4 / 系统菜单关闭 = 收起，别把窗口真关掉
                if self.on_close is not None:
                    self.on_close()
                    return 0
            elif msg == n.WM_APP_NATIVE:
                self._begin(int(wparam))
                return 0
            elif msg == n.WM_APP_FOCUS:
                self._grab()
                return 0
        except Exception as e:
            cfgmod.log(f"[wndproc] {e!r}")

        if self._old_proc:
            return n.user32.CallWindowProcW(
                self._old_proc, hwnd, msg, wparam, lparam
            )
        return n.user32.DefWindowProcW(hwnd, msg, wparam, lparam)

    def _hit_test(self, lparam: int) -> int:
        x = ctypes.c_short(lparam & 0xFFFF).value
        y = ctypes.c_short((lparam >> 16) & 0xFFFF).value

        left, top, w, h = self.window_rect()
        b = max(3, int(round(6 * self._scale)))

        on_left = x < left + b
        on_right = x >= left + w - b
        on_top = y < top + b
        on_bottom = y >= top + h - b

        if on_top and on_left:
            return n.HTTOPLEFT
        if on_top and on_right:
            return n.HTTOPRIGHT
        if on_bottom and on_left:
            return n.HTBOTTOMLEFT
        if on_bottom and on_right:
            return n.HTBOTTOMRIGHT
        if on_left:
            return n.HTLEFT
        if on_right:
            return n.HTRIGHT
        if on_top:
            return n.HTTOP
        if on_bottom:
            return n.HTBOTTOM
        return n.HTCLIENT

    # ---------------------------------------------------------------- 拖动 / 缩放

    def post_begin(self, hit: int) -> None:
        """线程安全：把「开始拖动/缩放」丢给 UI 线程。"""
        try:
            n.user32.PostMessageW(self.hwnd, n.WM_APP_NATIVE, hit, 0)
        except Exception as e:
            cfgmod.log(f"[frameless] post 失败：{e!r}")

    def _begin(self, hit: int) -> None:
        # 必须在本线程（UI 线程）里 ReleaseCapture，系统才会把鼠标交还给移动循环
        n.user32.ReleaseCapture()
        n.user32.SendMessageW(self.hwnd, n.WM_NCLBUTTONDOWN, hit, 0)

    # ---------------------------------------------------------------- 位置

    def window_rect(self) -> tuple[int, int, int, int]:
        r = n.wintypes.RECT()
        n.user32.GetWindowRect(self.hwnd, ctypes.byref(r))
        return r.left, r.top, r.right - r.left, r.bottom - r.top

    def set_bounds(self, x: int, y: int, w: int, h: int) -> None:
        n.user32.SetWindowPos(
            self.hwnd,
            None,
            x,
            y,
            w,
            h,
            n.SWP_NOZORDER | n.SWP_NOACTIVATE,
        )

    def place_center(self, width: int, height: int) -> None:
        """按逻辑尺寸（config 里的 width/height）摆到主屏中间。

        WinForms 自己按 DPI 换算出来的尺寸会有点出入，这里直接按物理像素算，
        配置写多少就是多少。
        """
        scale = self._scale
        w = max(320, int(round(width * scale)))
        h = max(320, int(round(height * scale)))
        wx, wy, ww, wh = n.work_area()
        self.set_bounds(wx + (ww - w) // 2, wy + (wh - h) // 2, w, h)

    # ---------------------------------------------------------------- 前台

    def is_foreground(self) -> bool:
        return n.user32.GetForegroundWindow() == self.hwnd

    def force_foreground(self) -> None:
        """线程安全：请 UI 线程把窗口抢到前台，并把键盘焦点交给页面。

        以前是在调用方线程里直接 SetForegroundWindow，那条路会被系统的前台锁
        （foreground lock）挡下来：窗口看得见，但键盘输入还在上一个程序里。
        现在改成 PostMessage 回 UI 线程，由窗口自己的线程去做，成功率高得多。
        """
        try:
            n.user32.PostMessageW(self.hwnd, n.WM_APP_FOCUS, 0, 0)
        except Exception as e:
            cfgmod.log(f"[focus] post 失败：{e!r}")

    def _grab(self) -> None:
        """在 UI 线程里：激活窗口 → 让页面拿到键盘焦点 → 通知上层。"""
        hwnd = self.hwnd
        try:
            if n.user32.IsIconic(hwnd):
                n.user32.ShowWindow(hwnd, n.SW_RESTORE)

            n.user32.BringWindowToTop(hwnd)
            if not self.is_foreground():
                self._activate(hwnd)

            self._focus_content()
        except Exception as e:
            cfgmod.log(f"[focus] {e!r}")

        if self.on_focus is not None:
            try:
                self.on_focus()
            except Exception as e:
                cfgmod.log(f"[focus] 回调失败：{e!r}")
    def _activate(self, hwnd: int) -> None:
        # 敲一下 Alt：让系统认为「用户刚有输入」，这是解除前台锁最省事的办法
        n.user32.keybd_event(n.VK_MENU, 0, 0, 0)
        n.user32.keybd_event(n.VK_MENU, 0, n.KEYEVENTF_KEYUP, 0)

        # 把本线程的输入队列挂到当前前台线程上，借它的前台权限
        fg = n.user32.GetForegroundWindow()
        tid_fg = n.user32.GetWindowThreadProcessId(fg, None) if fg else 0
        tid_me = n.kernel32.GetCurrentThreadId()
        attached = False
        try:
            if tid_fg and tid_fg != tid_me:
                attached = bool(n.user32.AttachThreadInput(tid_me, tid_fg, True))
            n.user32.SetForegroundWindow(hwnd)
            n.user32.BringWindowToTop(hwnd)
        finally:
            if attached:
                n.user32.AttachThreadInput(tid_me, tid_fg, False)

        if self.is_foreground():
            return

        n.switch_to_window(hwnd)
        if self.is_foreground():
            return

        # 系统还是不给面子：靠「先置顶再取消置顶」把窗口提到最上面
        n.user32.SetWindowPos(
            hwnd, n.HWND_TOPMOST, 0, 0, 0, 0, n.SWP_NOMOVE | n.SWP_NOSIZE
        )
        n.user32.SetWindowPos(
            hwnd, n.HWND_NOTOPMOST, 0, 0, 0, 0, n.SWP_NOMOVE | n.SWP_NOSIZE
        )
        n.user32.SetForegroundWindow(hwnd)

    def _focus_content(self) -> None:
        """把键盘焦点交给页面（WebView2 的子窗口）。

        只把顶层窗口拉到前台是不够的：焦点可能谁也不在，这时键盘输入进不了
        页面，Ctrl+F / Ctrl+Enter 这些页面里的快捷键也全都不响应。
        """
        if not self.is_foreground():
            return

        hwnd = self.hwnd
        child = n.user32.GetWindow(hwnd, n.GW_CHILD)
        target = child or hwnd
        if n.user32.GetFocus() == target:
            return
        if not n.user32.SetFocus(target):
            n.user32.SetFocus(hwnd)
