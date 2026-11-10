"""检测 Microsoft Edge WebView2 运行时是否可用。

pywebview 在 Windows 上只有 edgechromium 一个现代后端，而它本身不带渲染内核，
界面全靠系统里的 WebView2 运行时。缺少这个组件时启动脚本会毫无反应：`*_start.bat`
用 pythonw 拉起窗口，异常没有控制台可打，用户只会以为软件坏了。

所以启动脚本在拉起窗口之前先跑一次 `python -m manager.webview2 --launcher`，
缺了就按缺 Python 的方式停住并说明怎么装，而不是静默退出。

Microsoft 官方的判据是注册表里的 EdgeUpdate 客户端键（映射到已安装版本），
这里以它为准，目录扫描只作后备 —— 手动拷贝的绿色副本往往没有注册表记录，
那种情况 pywebview 一样起不来。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

RUNTIME_CLIENT = "{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"

# (根键, 子键) —— 机器级三十二位视图、机器级原生视图、当前用户级安装
_REG_KEYS = (
    ("HKLM", r"SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients"),
    ("HKLM", r"SOFTWARE\Microsoft\EdgeUpdate\Clients"),
    ("HKCU", r"Software\Microsoft\EdgeUpdate\Clients"),
)

_DIRS = (
    r"C:\Program Files (x86)\Microsoft\EdgeWebView\Application",
    r"C:\Program Files\Microsoft\EdgeWebView\Application",
)

INSTALL_ONLINE = "https://go.microsoft.com/fwlink/p/?LinkId=2124703"
INSTALL_PAGE = "https://developer.microsoft.com/microsoft-edge/webview2/"


def _usable(version: str) -> bool:
    version = (version or "").strip()
    return bool(version) and version != "0.0.0.0"


def _reg_version(root: str, path: str) -> str:
    try:
        import winreg
    except ImportError:
        return ""

    hive = winreg.HKEY_LOCAL_MACHINE if root == "HKLM" else winreg.HKEY_CURRENT_USER
    try:
        with winreg.OpenKey(hive, f"{path}\\{RUNTIME_CLIENT}") as key:
            value, _kind = winreg.QueryValueEx(key, "pv")
    except OSError:
        return ""
    return str(value).strip()


def _version_key(name: str) -> tuple[int, ...]:
    parts: list[int] = []
    for chunk in name.split("."):
        try:
            parts.append(int(chunk))
        except ValueError:
            parts.append(0)
    return tuple(parts)


def _dir_version() -> str:
    roots = list(_DIRS)
    local = os.environ.get("LOCALAPPDATA")
    if local:
        roots.append(str(Path(local) / "Microsoft" / "EdgeWebView" / "Application"))

    for root in roots:
        base = Path(root)
        if not base.is_dir():
            continue
        try:
            versions = sorted((p.name for p in base.iterdir() if p.is_dir()),
                              key=_version_key, reverse=True)
        except OSError:
            continue
        for name in versions:
            if (base / name / "msedgewebview2.exe").is_file():
                return name
    return ""


def version() -> str:
    """运行时可用就返回版本号，否则返回空字符串。"""
    if os.name != "nt":
        return ""

    for root, path in _REG_KEYS:
        found = _reg_version(root, path)
        if _usable(found):
            return found

    found = _dir_version()
    return found if _usable(found) else ""


def available() -> bool:
    return bool(version())


def _utf8_console() -> None:
    """控制台默认是 OEM 代码页，先切到 UTF-8，免得中文变成问号。"""
    try:
        import ctypes
        ctypes.windll.kernel32.SetConsoleOutputCP(65001)
    except Exception:
        pass
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def _report() -> None:
    print()
    print("未找到 Microsoft Edge WebView2 运行时，界面无法启动。")
    print()
    print("pywebview 在 Windows 上靠系统里的 WebView2 渲染，这个组件属于系统，")
    print("pip 装不上，需要单独安装一次：")
    print()
    print(f"  在线安装（约 2 MB，需联网）：{INSTALL_ONLINE}")
    print(f"  离线安装包（内网机器，约 150 MB）：{INSTALL_PAGE}")
    print()
    print("装完重新双击本启动脚本即可，不用重启电脑。")
    print()


def alert() -> None:
    """用系统弹窗说清楚缺什么。

    `*_start.bat` 用 pythonw 拉起窗口，没有控制台可打，检测失败只会在控制台
    一闪而过；弹窗才能让人真正看见，并顺手把下载页打开。
    """
    text = (
        "未找到 Microsoft Edge WebView2 运行时，界面无法启动。\n\n"
        "pywebview 在 Windows 上靠系统里的 WebView2 渲染界面，这个组件属于系统，"
        "pip 装不上，需要单独安装一次。\n\n"
        "在线安装包约 2 MB，需要联网；内网机器可用离线安装包（约 150 MB）。\n"
        "装完重新启动本程序即可，不用重启电脑。\n\n"
        "要现在打开官方下载页吗？"
    )
    try:
        import ctypes

        mb_yesno = 0x00000004
        mb_iconerror = 0x00000010
        mb_setforeground = 0x00010000
        mb_topmost = 0x00040000
        choice = ctypes.windll.user32.MessageBoxW(
            0,
            text,
            "缺少 WebView2 运行时",
            mb_yesno | mb_iconerror | mb_setforeground | mb_topmost,
        )
    except Exception:
        _utf8_console()
        _report()
        return

    if choice == 6:  # IDYES
        try:
            import webbrowser
            webbrowser.open(INSTALL_ONLINE)
        except Exception:
            pass


def ensure_available() -> bool:
    """启动前调用：可用返回 True，缺失时弹窗说明并返回 False。"""
    if available():
        return True
    alert()
    return False


def _pause() -> None:
    # 只有真的挂在控制台窗口上才值得等，被 Manager 或管道捕获时直接返回
    try:
        if not (sys.stdin.isatty() and sys.stdout.isatty()):
            return
    except (AttributeError, ValueError):
        return
    try:
        input("按回车键关闭窗口...")
    except (EOFError, KeyboardInterrupt):
        print()


def main(argv: list[str] | None = None) -> int:
    args = [a.lower() for a in (sys.argv[1:] if argv is None else argv)]
    found = version()

    if found:
        if "--quiet" not in args and "--launcher" not in args:
            print(f"WebView2 运行时 {found} 可用")
        return 0

    if "--quiet" in args:
        return 1

    _utf8_console()
    _report()
    if "--launcher" in args:
        _pause()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
