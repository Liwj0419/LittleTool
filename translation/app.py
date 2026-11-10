"""轻翻译：源码启动；--quit 独立退出，--selftest 无界面自检。"""
from __future__ import annotations

import sys


def main(argv: list[str] | None = None) -> int:
    args = [a.lower() for a in (sys.argv[1:] if argv is None else argv)]
    if "--selftest" in args:
        from lighttrans import selftest
        return selftest.run()

    from lighttrans.single import SingleInstance
    guard = SingleInstance()
    if "--reload" in args:
        return 0 if guard.request_reload() else 1
    if "--quit" in args:
        return 0 if guard.request_quit() else 1
    if "--shortcut" in args:
        from lighttrans.startup import create_shortcut
        return 0 if create_shortcut(force="--force-shortcut" in args) else 1
    if not guard.acquire():
        return 0 if ("--hidden" in args and "--show" not in args) or guard.signal() else 1

    try:
        from lighttrans.core import App
        app = App(hidden=("--hidden" in args and "--show" not in args), debug="--devtools" in args)
        guard.listen(app.show_window, app.quit, app.reload_config)
        app.run()
    finally:
        guard.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
