"""工具箱管理器：源码启动；--quit 独立退出，--selftest 无界面自检。"""
from __future__ import annotations

import sys


def main(argv: list[str] | None = None) -> int:
    args = [a.lower() for a in (sys.argv[1:] if argv is None else argv)]
    if "--selftest" in args:
        from manager import selftest
        return selftest.run()

    # 界面由系统 WebView2 渲染，缺了 pywebview 直接起不来；pythonw 没有控制台，
    # 不在这里拦一下就是「双击毫无反应」。
    if "--quit" not in args and "--reload" not in args:
        from manager import config as cfgmod
        from manager import webview2

        if not webview2.ensure_available():
            cfgmod.log("[start] 缺少 WebView2 运行时，已弹窗提示安装方式")
            return 1

    from manager.single import SingleInstance
    guard = SingleInstance()
    if "--reload" in args:
        return 0 if guard.request_reload() else 1
    if "--quit" in args:
        return 0 if guard.request_quit() else 1
    if not guard.acquire():
        return 0 if ("--hidden" in args and "--show" not in args) or guard.signal() else 1

    try:
        from manager.core import App
        app = App(hidden=("--hidden" in args and "--show" not in args), debug="--devtools" in args)
        guard.listen(app.show_window, app.quit, app.reload_config)
        app.run()
    finally:
        guard.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
