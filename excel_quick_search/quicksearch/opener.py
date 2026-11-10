"""打开 / 定位文件。

优先交给同级的 excel_shower 查看器（它就是为这件事写的），
找不到、没装或者调用出错时退回系统默认程序 —— 和旧版行为一致。
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from . import config as cfgmod

def _shower_dir(cfg) -> Path:
    raw = str((cfg.open or {}).get("shower_dir") or "../excel_shower")
    path = Path(raw)
    if not path.is_absolute():
        path = (cfgmod.app_dir() / path).resolve()
    return path


def _shower_command(cfg):
    """仅调用对方公开入口和自己的解释器，不加载兄弟目录的 Python 模块。"""
    if not (cfg.open or {}).get("prefer_shower", True):
        return None
    folder = _shower_dir(cfg)
    if not (folder / "app.py").is_file():
        return None
    for relative in (".venv/Scripts/pythonw.exe", ".venv/Scripts/python.exe", ".venv/bin/python"):
        python = folder / relative
        if python.is_file():
            return folder, python
    return None


def open_path(path: str, cfg) -> dict:
    """打开一个文件。返回 {ok, how, error}。"""
    if not path or not os.path.exists(path):
        return {"ok": False, "how": "", "error": f"文件不存在：{path}"}

    target = _shower_command(cfg)
    if target is not None:
        try:
            from .native import allow_foreground
            allow_foreground()
            folder, python = target
            subprocess.Popen([str(python), str(folder / "app.py"), os.path.abspath(path)],
                             cwd=str(folder), creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            return {"ok": True, "how": "shower", "error": ""}
        except Exception as exc:
            cfgmod.log(f"[excel_quick_search] 查看器启动失败，退回系统默认：{exc!r}")

    try:
        if sys.platform == "win32":
            os.startfile(path)  # noqa: S606
        elif sys.platform == "darwin":
            subprocess.Popen(["open", path])
        else:
            subprocess.Popen(["xdg-open", path])
        return {"ok": True, "how": "system", "error": ""}
    except Exception as e:
        cfgmod.log(f"[open] 打开失败：{e!r}")
        return {"ok": False, "how": "", "error": str(e)}


def reveal(path: str) -> bool:
    """在文件管理器里定位这个文件。"""
    if not path or not os.path.exists(path):
        return False
    target = os.path.normpath(path)
    try:
        if sys.platform == "win32":
            # explorer 的 /select 参数要求路径紧跟逗号，不能拆成两个参数
            subprocess.Popen(["explorer", f"/select,{target}"])
        elif sys.platform == "darwin":
            subprocess.Popen(["open", "-R", target])
        else:
            subprocess.Popen(["xdg-open", os.path.dirname(target) or "."])
        return True
    except Exception as e:
        cfgmod.log(f"[reveal] {e!r}")
        return False
