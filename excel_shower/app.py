"""Excel 数据查看器 —— 常驻后台的只读表格窗口。

底层：pywebview（窗口）+ pynput（全局热键）。

    python app.py                    打开空窗口（已在运行则唤起它）
    python app.py 表格.xlsx           直接打开某个文件
    python app.py --hidden           静默待命
    python app.py --devtools         带开发者工具
    python app.py --selftest         无界面自检：写 selftest.log 后退出
    python app.py --dump 表格.xlsx    不开界面，只打印解析结果
"""

from __future__ import annotations

import os
import sys

from excelshower import ipc, native


def _fatal(message: str) -> None:
    """没有控制台时（pythonw）也要让人看见错误。"""
    try:
        if sys.platform == "win32":
            import ctypes

            ctypes.windll.user32.MessageBoxW(None, message, "Excel 数据查看器", 0x10)
            return
    except Exception:
        pass
    print(message, file=sys.stderr)


def _dump(path: str) -> int:
    from excelshower.sheet import load_table

    t = load_table(path)
    print(f"文件: {t.path}")
    print(f"表名: {t.sheet!r}   行: {t.nrows}   列: {t.ncols}   "
          f"耗时: {t.elapsed:.3f}s   截断: {t.truncated}")
    print("表头:", t.headers)
    for row in t.rows[:5]:
        print("  ", row)
    return 0


def main(argv: list[str] | None = None) -> int:
    args = [a for a in (sys.argv[1:] if argv is None else argv) if a]
    flags = {a.lower() for a in args if a.startswith("--")}
    paths = [a for a in args if not a.startswith("--")]

    if "--selftest" in flags:
        from excelshower import selftest
        return selftest.run()
    from excelshower.single import SingleInstance
    guard = SingleInstance()
    if "--reload" in flags:
        return 0 if guard.request_reload() else 1
    if "--quit" in flags:
        return 0 if guard.request_quit() else 1
    native.set_dpi_aware()

    if "--dump" in flags:
        if not paths:
            print("用法: app.py --dump <文件>")
            return 2
        return _dump(paths[0])

    initial = os.path.abspath(paths[0]) if paths else None

    try:
        import webview  # noqa: F401
    except Exception as e:
        _fatal("缺少 pywebview，界面起不来。\n\n"
               "在项目目录里执行：\n"
               "    .venv\\Scripts\\pip install -r requirements.txt\n\n"
               f"（{e!r}）")
        return 1

    # The mutex also covers the interval before the IPC server is ready.
    if not guard.acquire():
        native.allow_foreground()
        if initial:
            return 0 if ipc.send("OPEN", initial) else 1
        return 0 if ("--hidden" in flags and "--show" not in flags) or guard.signal() else 1

    from excelshower.core import App

    app = App(
        hidden="--hidden" in flags and "--show" not in flags,
        debug="--devtools" in flags,
        initial=initial,
    )
    guard.listen(app.show_window, app.quit, app.reload_config)
    try:
        app.run()
    finally:
        guard.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
