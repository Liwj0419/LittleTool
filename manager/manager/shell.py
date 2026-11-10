"""无边框窗口的原生行为：四边缩放、顶栏拖动、抢到前台。

pywebview 的无边框窗口本身不带缩放边框，这里给窗口补上 WS_THICKFRAME，
并把 WM_NCCALCSIZE 的边框扣掉（客户区仍然铺满整个窗口），
拖动 / 缩放则用「PostMessage 一个自定义消息 → UI 线程里发 WM_NCLBUTTONDOWN」
交给系统自己的移动 / 缩放循环，比用 JS 一点点挪窗口顺滑。

窗口只支持拖动和缩放，不做最大化：双击顶栏不放大，也没有最大化按钮。
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
    def __init__(self, hwnd: int, on_close=None, min_size=(740, 460)) -> None:
        self.hwnd = hwnd
        self.on_close = on_close  # 收到 WM_CLOSE 时干什么（本项目选择「收进托盘」）
        self.min_width, self.min_height = min_size
        self._old_proc: int = 0
        self._proc = n.WNDPROC(self._wndproc)  # 必须留引用，否则回调会被回收
        self._scale = n.dpi_scale(hwnd)
        self._attach()

    # ---------------------------------------------------------------- 安装

    def _attach(self) -> None:
        if not n.IS_WIN:
            return
        try:
            style = n.user32.GetWindowLongPtrW(self.hwnd, n.GWL_STYLE)
            # 要缩放边框，但不要最大化框（系统菜单里也就不出现「最大化」）
            wanted = (style | n.WS_THICKFRAME) & ~n.WS_MAXIMIZEBOX
            if wanted != style:
                n.user32.SetWindowLongPtrW(self.hwnd, n.GWL_STYLE, wanted)

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
        if not n.IS_WIN or not self._old_proc:
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
            if msg == n.WM_NCCALCSIZE:
                if wparam:
                    return 0  # 客户区 = 整个窗口，原生边框一点不留
            elif msg == n.WM_GETMINMAXINFO:
                self._clamp_min_track(lparam)
                return 0
            elif msg == n.WM_NCHITTEST:
                if n.user32.IsZoomed(self.hwnd):
                    return n.HTCLIENT  # 万一被系统放大，别再给缩放边框
                return self._hit_test(lparam)
            elif msg == n.WM_NCLBUTTONDBLCLK:
                return 0  # 双击顶栏不放大
            elif msg == n.WM_SYSCOMMAND:
                if (int(wparam) & 0xFFF0) == n.SC_MAXIMIZE:
                    return 0  # 窗口只拖动 / 缩放，不做最大化
            elif msg == n.WM_CLOSE:
                # Alt+F4 / 系统菜单关闭 = 收进托盘（进程继续跑）
                if self.on_close is not None:
                    self.on_close()
                    return 0
            elif msg == n.WM_APP_NATIVE:
                self._begin(int(wparam))
                return 0
        except Exception as e:
            cfgmod.log(f"[wndproc] {e!r}")

        if self._old_proc:
            return n.user32.CallWindowProcW(self._old_proc, hwnd, msg, wparam, lparam)
        return n.user32.DefWindowProcW(hwnd, msg, wparam, lparam)

    def _clamp_min_track(self, lparam: int) -> None:
        try:
            info = ctypes.cast(lparam, ctypes.POINTER(n.MINMAXINFO)).contents
            # 这个分支直接 return 0，WinForms 的 MinimumSize 不会生效，
            # 拖边缩放的最小尺寸得自己兜住（和 create_window 的 min_size 一致）
            info.ptMinTrackSize.x = int(round(self.min_width * self._scale))
            info.ptMinTrackSize.y = int(round(self.min_height * self._scale))
        except Exception as e:
            cfgmod.log(f"[frameless] 最小尺寸修正失败：{e!r}")

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
        if not n.IS_WIN:
            return
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
        if not n.IS_WIN:
            return 0, 0, 0, 0
        r = n.wintypes.RECT()
        n.user32.GetWindowRect(self.hwnd, ctypes.byref(r))
        return r.left, r.top, r.right - r.left, r.bottom - r.top

    def set_bounds(self, x: int, y: int, w: int, h: int) -> None:
        if not n.IS_WIN:
            return
        n.user32.SetWindowPos(
            self.hwnd, None, x, y, w, h, n.SWP_NOZORDER | n.SWP_NOACTIVATE
        )

    def place_center(self, width: int, height: int) -> None:
        """按逻辑尺寸（config 里的 width/height）摆到主屏中间。"""
        if not n.IS_WIN:
            return
        scale = self._scale
        w = max(self.min_width, int(round(width * scale)))
        h = max(self.min_height, int(round(height * scale)))
        wx, wy, ww, wh = n.work_area()
        w = min(w, ww)
        h = min(h, wh)
        self.set_bounds(wx + (ww - w) // 2, wy + (wh - h) // 2, w, h)

    # ---------------------------------------------------------------- 最小化

    def minimize(self) -> None:
        try:
            n.user32.ShowWindow(self.hwnd, n.SW_MINIMIZE)
        except Exception as e:
            cfgmod.log(f"[frameless] 最小化失败：{e!r}")

    # ---------------------------------------------------------------- 前台

    def force_foreground(self, allow_alt: bool = True) -> bool:
        """从后台进程把窗口抢到最前面。"""
        return force_foreground(self.hwnd, allow_alt)


def force_foreground(hwnd: int, allow_alt: bool = True) -> bool:
    """把任意窗口抢到前台。

    系统只允许"当前前台进程"改前台，所以先 AttachThreadInput 蹭一下对方的
    输入队列；还不行就用 topmost 闪一下，最后兜底模拟一次 Alt 敲击。
    启动器（Alt+Space 弹出）和 Manager 主窗口都用这一套。
    """
    if not n.IS_WIN or not hwnd:
        return True
    if n.user32.GetForegroundWindow() == hwnd:
        return True

    fg = n.user32.GetForegroundWindow()
    tid_fg = n.user32.GetWindowThreadProcessId(fg, None) if fg else 0
    tid_me = n.kernel32.GetCurrentThreadId()
    attached = False
    try:
        if tid_fg and tid_fg != tid_me:
            attached = bool(n.user32.AttachThreadInput(tid_me, tid_fg, True))
        n.user32.SetForegroundWindow(hwnd)
    finally:
        if attached:
            n.user32.AttachThreadInput(tid_me, tid_fg, False)

    if n.user32.GetForegroundWindow() == hwnd:
        return True

    n.user32.SetWindowPos(
        hwnd, n.HWND_TOPMOST, 0, 0, 0, 0, n.SWP_NOMOVE | n.SWP_NOSIZE
    )
    n.user32.SetWindowPos(
        hwnd, n.HWND_NOTOPMOST, 0, 0, 0, 0, n.SWP_NOMOVE | n.SWP_NOSIZE
    )
    n.user32.SetForegroundWindow(hwnd)

    if n.user32.GetForegroundWindow() == hwnd:
        return True
    if not allow_alt:
        return False

    # 模拟一次 Alt 敲击：本进程就成了「最后一个输入事件的拥有者」，前台锁才松
    n.user32.keybd_event(n.VK_MENU, 0, 0, None)
    n.user32.keybd_event(n.VK_MENU, 0, n.KEYEVENTF_KEYUP, None)
    n.user32.SetForegroundWindow(hwnd)
    return n.user32.GetForegroundWindow() == hwnd
