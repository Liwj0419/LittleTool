"""进程侧的全部脏活：认出谁在跑、谁把窗口摆出来了、启动谁、停掉谁。

只认「软件目录下 .venv 里的 python」这一条：每个软件的 venv 路径都不一样，
所以扫一遍进程的可执行文件路径就能分辨出哪一行在后台待命，
不依赖各软件自己的单实例端口 / 互斥体名字。
"""

from __future__ import annotations

import ctypes
import os
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from . import config as cfgmod
from . import native as n

# 启动子进程时不弹控制台、也不跟着 Manager 一起退出
CREATE_NO_WINDOW = 0x08000000
DETACHED_PROCESS = 0x00000008
CREATE_NEW_PROCESS_GROUP = 0x00000200


@dataclass(frozen=True)
class ProcInfo:
    pid: int
    ppid: int
    exe: str  # 全路径，取不到就是空串


def _exe_path(pid: int) -> str:
    handle = n.kernel32.OpenProcess(n.PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return ""
    try:
        size = ctypes.c_ulong(1024)
        buf = ctypes.create_unicode_buffer(1024)
        if n.kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
            return buf.value
        return ""
    finally:
        n.kernel32.CloseHandle(handle)


def snapshot() -> list[ProcInfo]:
    """一次进程快照：pid / 父 pid / 可执行文件全路径。"""
    if not n.IS_WIN:
        return []

    snap = n.kernel32.CreateToolhelp32Snapshot(n.TH32CS_SNAPPROCESS, 0)
    if not snap or int(snap) == -1:
        return []

    out: list[ProcInfo] = []
    try:
        entry = n.PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(n.PROCESSENTRY32W)
        if not n.kernel32.Process32FirstW(snap, ctypes.byref(entry)):
            return []
        while True:
            pid = int(entry.th32ProcessID)
            if pid:
                out.append(
                    ProcInfo(pid, int(entry.th32ParentProcessID), _exe_path(pid))
                )
            if not n.kernel32.Process32NextW(snap, ctypes.byref(entry)):
                break
    finally:
        n.kernel32.CloseHandle(snap)
    return out


def _is_python(exe: str) -> bool:
    name = os.path.basename(exe).lower()
    return name in ("python.exe", "pythonw.exe")


def pids_of(app_path: Path, procs: list[ProcInfo] | None = None) -> list[int]:
    """工具自己的解释器和 WebView 运行进程，不接管它打开的其它应用。"""
    if not n.IS_WIN:
        return []
    root = os.path.normcase(str(Path(app_path) / ".venv")) + os.sep
    procs = snapshot() if procs is None else procs
    found = set()
    for p in procs:
        if not p.exe or not _is_python(p.exe):
            continue
        if os.path.normcase(p.exe).startswith(root):
            found.add(p.pid)

    def independent_tool(proc):
        if not proc.exe or not _is_python(proc.exe):
            return False
        try:
            parts = Path(proc.exe).relative_to(app_path.parent).parts
            return len(parts) > 2 and parts[0] != app_path.name and parts[1] == ".venv"
        except ValueError:
            return False

    by_pid = {p.pid: p for p in procs}

    def runtime_child(proc):
        if os.path.normcase(proc.exe).startswith(root):
            return True
        name = os.path.basename(proc.exe).lower()
        if name in {"msedgewebview2.exe", "crashpad_handler.exe"}:
            return True
        # Windows' venv launcher starts the base Python executable as a child.
        parent = by_pid.get(proc.ppid)
        return bool(parent and _is_python(proc.exe)
                    and os.path.normcase(parent.exe).startswith(root))

    while True:
        children = {p.pid for p in procs if p.ppid in found and p.pid not in found
                    and not independent_tool(p) and runtime_child(p)}
        if not children:
            break
        found.update(children)
    return sorted(found)


def visible_window(pid: int) -> str:
    """这个进程有没有露出窗口；返回窗口标题，没有就是空串。"""
    if not n.IS_WIN:
        return ""
    titles: list[str] = []
    proc = ctypes.WINFUNCTYPE(
        ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p
    )(lambda hwnd, _lparam: _collect(hwnd, pid, titles))

    n.user32.EnumWindows(proc, None)
    return titles[0] if titles else ""


def _collect(hwnd, pid: int, titles: list[str]) -> bool:
    try:
        owner = ctypes.c_ulong(0)
        n.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        if int(owner.value) != pid:
            return True
        if not n.user32.IsWindowVisible(hwnd) or n.user32.IsIconic(hwnd):
            return True
        length = n.user32.GetWindowTextLengthW(hwnd)
        if length <= 0:
            return True
        buf = ctypes.create_unicode_buffer(length + 1)
        n.user32.GetWindowTextW(hwnd, buf, length + 1)
        if buf.value.strip():
            titles.append(buf.value)
    except Exception:
        pass
    return True


# --------------------------------------------------------------------------- 启动


def _launch_env() -> dict:
    env = os.environ.copy()
    # Do not inject Manager modules or sitecustomize into another tool.
    return env


def can_start(app) -> tuple[bool, str]:
    python = app.python_exe()
    if python is None:
        return False, "还没有 .venv（在 Manager 里点「运行」会自动装）"
    if not app.app_py.exists():
        return False, "找不到 app.py"
    return True, ""


def start(app, show: bool = False) -> tuple[bool, str]:
    ok, message = can_start(app)
    if not ok:
        return False, message

    python = app.python_exe()
    args = [str(python), str(app.app_py)]
    args += ["--show"] if (show or app.mode == "window") else ["--hidden"]

    try:
        subprocess.Popen(
            args,
            cwd=str(app.path),
            env=_launch_env(),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=CREATE_NO_WINDOW | DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP,
            close_fds=True,
        )
    except Exception as e:
        cfgmod.log(f"[start] {app.id} 失败：{e!r}")
        return False, f"启动失败：{e}"

    cfgmod.log(f"[start] {app.id} show={show}")
    return True, ""


# --------------------------------------------------------------------------- 停止


def stop(app, timeout: float = 3.0) -> tuple[bool, str]:
    pids = pids_of(app.path)
    if not pids:
        return True, ""

    python = app.python_exe(console=True)
    if python is not None:
        try:
            subprocess.run([str(python), str(app.app_py), "--quit"], cwd=str(app.path),
                           stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL, creationflags=CREATE_NO_WINDOW,
                           timeout=3.0, check=False)
        except (OSError, subprocess.TimeoutExpired):
            pass
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if not pids_of(app.path):
                return True, ""
            time.sleep(0.08)

    procs = snapshot()
    owned = set(pids_of(app.path, procs))
    children: dict[int, list[int]] = {}
    for p in procs:
        children.setdefault(p.ppid, []).append(p.pid)

    targets: list[int] = []
    visited: set[int] = set()

    def walk(pid: int) -> None:
        if pid in visited or pid not in owned:
            return
        visited.add(pid)
        for child in children.get(pid, []):
            walk(child)
        targets.append(pid)

    for pid in pids:
        walk(pid)

    for pid in targets:
        handle = n.kernel32.OpenProcess(n.PROCESS_TERMINATE, False, pid)
        if not handle:
            continue
        try:
            n.kernel32.TerminateProcess(handle, 1)
        finally:
            n.kernel32.CloseHandle(handle)

    cfgmod.log(f"[stop] {app.id} pids={targets}")

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not pids_of(app.path):
            return True, ""
        time.sleep(0.08)
    return False, "进程没有按时退出"
