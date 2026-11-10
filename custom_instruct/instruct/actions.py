"""真正干活的部分：打开路径、叫出 TortoiseSVN 的窗口。"""

from __future__ import annotations

import os
import subprocess
import sys

IS_WINDOWS = sys.platform == "win32"

TORTOISE_NAMES = ("TortoiseProc.exe",)

# TortoiseSVN 装完一般就在这两个地方，PATH 里没有时兜底
TORTOISE_FALLBACKS = (
    r"C:\Program Files\TortoiseSVN\bin\TortoiseProc.exe",
    r"C:\Program Files (x86)\TortoiseSVN\bin\TortoiseProc.exe",
)

_tortoise_cache: str | None = None
_tortoise_looked = False


def find_tortoise() -> str | None:
    """先在 PATH 里找，再试两个常见安装目录。"""
    global _tortoise_cache, _tortoise_looked
    if _tortoise_looked:
        return _tortoise_cache

    _tortoise_looked = True
    if not IS_WINDOWS:
        return None

    for part in os.environ.get("PATH", "").split(os.pathsep):
        directory = part.strip().strip('"')
        if not directory:
            continue
        for name in TORTOISE_NAMES:
            candidate = os.path.join(directory, name)
            try:
                if os.path.isfile(candidate):
                    _tortoise_cache = candidate
                    return candidate
            except OSError:
                continue

    for candidate in TORTOISE_FALLBACKS:
        if os.path.isfile(candidate):
            _tortoise_cache = candidate
            return candidate

    return None


def open_path(path: str) -> str | None:
    """文件夹交给资源管理器，文件交给默认程序。返回错误文案，成功返回 None。"""
    try:
        if IS_WINDOWS:
            os.startfile(path)  # noqa: S606 - 这就是要的效果
        elif sys.platform == "darwin":
            subprocess.Popen(["open", path])
        else:
            subprocess.Popen(["xdg-open", path])
    except Exception as e:
        return f"打不开：{path}\n\n{e}"
    return None


def tortoise_missing() -> str | None:
    """TortoiseProc 找不到时返回提示文案，能找到返回 None。"""
    if find_tortoise() is not None:
        return None
    return (
        "没找到 TortoiseProc.exe。\n\n"
        "装了 TortoiseSVN 的话，把它的 bin 目录加进 PATH 就能用了。"
    )


def tortoise(command: str, folder: str) -> str | None:
    """把文件夹交给 TortoiseSVN 自己的窗口，返回错误文案，成功返回 None。"""
    proc = find_tortoise()
    if proc is None:
        return tortoise_missing()

    verb = "update" if command == "svn update" else "commit"
    try:
        # 用参数列表，路径里的空格和中文都不用自己拼引号
        subprocess.Popen([proc, f"/command:{verb}", f"/path:{folder}", "/closeonend:0"])
    except Exception as e:
        return f"没能打开 TortoiseSVN 的{verb}窗口：\n\n{e}"
    return None


def beep() -> None:
    """没匹配到指令时响一声（Windows 上走系统提示音）。"""
    try:
        if IS_WINDOWS:
            import winsound

            winsound.MessageBeep(0x00000030)  # MB_ICONWARNING
        else:
            sys.stdout.write("\a")
            sys.stdout.flush()
    except Exception:
        pass
