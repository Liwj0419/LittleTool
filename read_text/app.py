#!/usr/bin/env python3
"""read_text —— 锁定屏幕上的图片，把它里面的文字当文本拖选复制。

    python app.py                # 后台常驻，按热键取字
    python app.py --hidden       # 同上（本工具没有主窗口，参数只为统一入口）
    python app.py --devtools     # 带开发者工具
    python app.py --selftest     # 自检：跑一遍识别链路，写 selftest.log 后退出
    python app.py --where        # 看一眼现在按热键会锁哪个窗口（不开界面）
"""

from __future__ import annotations

import os
import sys


def _flag(args: list[str], name: str) -> bool:
    return name in args


def main(argv: list[str] | None = None) -> int:
    raw = sys.argv[1:] if argv is None else argv
    args = [a.lower() if a.startswith("--") else a for a in raw]

    # DPI 必须在任何窗口创建之前定下来，否则坐标会被系统再缩放一次
    from read_text import screen

    screen.enable_dpi_awareness()

    if _flag(args, "--selftest"):
        from read_text import selftest

        return selftest.run()

    if _flag(args, "--where"):
        # 只看一眼「现在按热键会锁哪个窗口」，不启动任何东西
        info = screen.window_info_at_cursor()
        if not info:
            print("鼠标下面没有可锁的窗口（桌面/任务栏不算）")
            return 1
        x, y, w, h = info["rect"]
        vx, vy, vw, vh = screen.virtual_screen()
        print("窗口类名 : %s" % info["class"])
        print("窗口标题 : %s" % (info["title"] or "(无标题)"))
        print("原始矩形 : %dx%d @ (%d,%d)" % (info["window_rect"][2], info["window_rect"][3],
                                              info["window_rect"][0], info["window_rect"][1]))
        print("实际锁定 : %dx%d @ (%d,%d)" % (w, h, x, y))
        print("整块桌面 : %dx%d，锁定占 %.0f%%" % (vw, vh, 100.0 * w * h / max(vw * vh, 1)))
        return 0

    from read_text import config as cfgmod
    from read_text.single import Single

    cfgmod.log("[app] 启动 pid=%d args=%s" % (os.getpid(), args))

    guard = Single()
    if _flag(args, "--reload"):
        return 0 if guard.request_reload() else 1
    if _flag(args, "--quit"):
        return 0 if guard.request_quit() else 1
    if not guard.acquire():
        cfgmod.log("[read_text] 已有实例在后台待命")
        return 0

    try:
        from read_text.core import App
        app = App(hidden=True, debug=_flag(args, "--devtools"))
        guard.listen(lambda: None, app.quit, app.reload_config)
        app.run()
    finally:
        guard.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
