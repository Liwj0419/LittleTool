"""按用户明确请求创建桌面快捷方式；不设置开机自启。

快捷方式指向本工具自己的 pythonw.exe 和 app.py，不编译打包。
快捷方式走 lighttrans.shortcut（纯 ctypes 调 IShellLink），不依赖 PowerShell。
注意：快捷方式**不会自动创建**，只在 `app.py --shortcut` 时按需生成。
"""

from __future__ import annotations

import os
import sys
import winreg
from pathlib import Path

from . import config as cfgmod
from . import shortcut

SHORTCUT_NAME = "翻译.lnk"
SHORTCUT_DESC = "打开轻翻译"


def launch_target() -> tuple[str, str, str]:
    """启动这个程序需要什么： (可执行文件, 参数, 图标)。"""
    exe = Path(sys.executable).resolve()

    # 源码运行：优先 pythonw.exe（不弹控制台），参数是 app.py 的绝对路径
    pythonw = exe.with_name("pythonw.exe")
    if pythonw.exists():
        exe = pythonw

    script = cfgmod.app_dir() / "app.py"
    icon = cfgmod.assets_dir() / "icon.ico"
    return str(exe), f'"{script}"', str(icon) if icon.exists() else str(exe)


# --------------------------------------------------------------------------- 快捷方式


def _desktop_dir() -> Path:
    """桌面真实位置（可能被 OneDrive / 组策略重定向过）。"""
    for key_path in (
        r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders",
        r"Software\Microsoft\Windows\CurrentVersion\Explorer\Shell Folders",
    ):
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path) as key:
                raw, _ = winreg.QueryValueEx(key, "Desktop")
            path = Path(os.path.expandvars(raw))
            if path.is_dir():
                return path
        except OSError:
            pass

    onedrive = os.environ.get("OneDrive") or os.environ.get("OneDriveConsumer")
    if onedrive:
        cand = Path(onedrive) / "Desktop"
        if cand.is_dir():
            return cand
    return Path.home() / "Desktop"


def _same_target(lnk: Path, exe: str, args: str) -> bool:
    """桌面那条 .lnk 现在是不是正好指向本目录的这份程序。"""
    target, target_args = shortcut.read(lnk)
    if not target:
        return False
    return target.lower() == exe.lower() and target_args.strip() == args.strip()


def create_shortcut(force: bool = False) -> bool:
    """同步创建 / 修复桌面快捷方式，返回是否真的可用。

    判断依据是 .lnk 里真正写着什么，而不是某个「已经建过」的标记文件 ——
    旧版先写标记再看结果，PowerShell 静默失败时就留下「标记说建过、桌面没有」
    的假状态，之后再也不重试。现在每次都读一遍 .lnk（毫秒级），
    再次显式运行 --shortcut 时会核对并修复目标。
    """
    try:
        exe, args, icon = launch_target()
        lnk = _desktop_dir() / SHORTCUT_NAME

        if not force and lnk.exists() and _same_target(lnk, exe, args):
            return True

        shortcut.create(
            lnk,
            target=exe,
            arguments=args,
            workdir=str(cfgmod.app_dir()),
            description=SHORTCUT_DESC,
            icon=icon,
            icon_index=0,
        )

        if not lnk.exists():
            cfgmod.log(f"[shortcut] 写完了但文件不在：{lnk}")
            return False

        cfgmod.log(f"[shortcut] 已生成 {lnk}")
        return True
    except Exception as e:
        cfgmod.log(f"[shortcut] 创建失败：{e!r}")
        return False
