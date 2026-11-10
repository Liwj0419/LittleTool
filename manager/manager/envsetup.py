"""在目标工具自己的 `.venv` 建环境、核对并安装 requirements.txt。

界面上的「运行」开关如果发现某行没有 `.venv`，就会在后台线程里调 `ensure()`，
装完再启动；进度通过 `note` 回调一路冒到界面的状态列上，不弹控制台窗口。

选哪个 Python 建环境：优先用 Manager 自己的解释器（它能把这些软件跑起来，
版本肯定是合适的），其次 `py -3`，最后 PATH 里的 `python` / `python3`。
"""

from __future__ import annotations

import os
import queue
import shutil
import subprocess
import sys
import threading
import time
from collections import deque

from . import config as cfgmod

CREATE_NO_WINDOW = 0x08000000
VENV_TIMEOUT = 300.0
PIP_TIMEOUT = 1800.0


def _child_env() -> dict:
    env = os.environ.copy()
    # pythonw 下没有控制台，stdout 默认整块缓冲：不强制无缓冲，进度行会迟到很久
    env["PYTHONUNBUFFERED"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    env["PIP_DISABLE_PIP_VERSION_CHECK"] = "1"
    env.pop("PYTHONPATH", None)  # 别把 Manager 的垫片带进 pip 进程
    return env


def _candidates() -> list[list[str]]:
    """按可信度排好的候选解释器命令。"""
    out: list[list[str]] = []
    seen: set[str] = set()

    def add(cmd: list[str], key: str) -> None:
        key = os.path.normcase(key)
        if key and key not in seen:
            seen.add(key)
            out.append(cmd)

    if sys.executable:
        add([sys.executable], sys.executable)

    py_launcher = shutil.which("py")
    if py_launcher:
        add([py_launcher, "-3"], "py -3")

    for name in ("python", "python3"):
        found = shutil.which(name)
        if found:
            add([found], found)

    return out


def _run(cmd: list[str], cwd, timeout: float, on_line=None) -> tuple[int, str]:
    """跑一条命令，逐行喂给 on_line；返回 (退出码, 末尾输出)。"""
    tail: deque[str] = deque(maxlen=60)
    try:
        proc = subprocess.Popen(
            cmd,
            cwd=str(cwd),
            env=_child_env(),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=CREATE_NO_WINDOW if os.name == "nt" else 0,
            start_new_session=os.name != "nt",
        )
    except OSError as e:
        return -1, f"启动失败：{e}"

    lines = queue.Queue()

    def read_output():
        try:
            if proc.stdout is not None:
                for line in proc.stdout:
                    lines.put(line.rstrip())
        finally:
            lines.put(None)

    reader = threading.Thread(target=read_output, name="install-output", daemon=True)
    reader.start()
    deadline = time.monotonic() + timeout
    try:
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise subprocess.TimeoutExpired(cmd, timeout)
            try:
                line = lines.get(timeout=min(0.2, remaining))
            except queue.Empty:
                continue
            if line is None:
                break
            if line:
                tail.append(line)
                if on_line is not None:
                    try:
                        on_line(line)
                    except Exception as exc:
                        cfgmod.log(f"[env] 安装进度回调失败：{exc!r}")
        code = proc.wait(timeout=max(0.01, deadline - time.monotonic()))
    except subprocess.TimeoutExpired:
        # On Windows a venv launcher has a base-Python child which owns the
        # stdout pipe. Killing only the launcher leaves that child running.
        if os.name == "nt":
            try:
                subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                               stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL, creationflags=CREATE_NO_WINDOW,
                               timeout=8, check=False)
            except (OSError, subprocess.TimeoutExpired):
                pass
        else:
            import signal
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        if proc.poll() is None:
            proc.kill()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            pass
        return -1, "安装超时，已中断（网络太慢或包太大）"
    finally:
        reader.join(timeout=1)
        if not reader.is_alive() and proc.stdout is not None:
            proc.stdout.close()
    return code, "\n".join(tail)


def create(app, note) -> tuple[bool, str]:
    """建 `.venv`。已经能用了就直接返回。"""
    if app.python_exe() is not None:
        return True, ""

    note("创建虚拟环境")
    venv = app.path / ".venv"
    errors: list[str] = []
    for base in _candidates():
        code, out = _run([*base, "-m", "venv", str(venv)], app.path, VENV_TIMEOUT)
        if app.python_exe() is not None:
            cfgmod.log(f"[env] {app.id} venv 建好了：{' '.join(base)}")
            return True, ""
        errors.append(f"{' '.join(base)} → rc={code}：{out.strip()[-300:]}")

    detail = "\n".join(errors) or "没找到可用的 Python"
    return False, "创建虚拟环境失败，先确认装了 Python 3.10+：\n" + detail


def install_deps(app, note) -> tuple[bool, str]:
    """装 requirements.txt。pip 每报一个包，就把包名顶到状态列上。"""
    python = app.python_exe(console=True)
    if python is None:
        return False, "虚拟环境没建起来"

    req = app.path / "requirements.txt"
    if not req.exists():
        return True, ""

    note("安装依赖")

    def on_line(line: str) -> None:
        if line.startswith("Collecting "):
            pkg = line[len("Collecting "):].strip().split(" ", 1)[0]
            pkg = pkg.split("==", 1)[0].split(">=", 1)[0]
            if pkg:
                note(f"安装 {pkg}")
        elif line.startswith("Installing collected packages"):
            note("写入依赖")

    code, out = _run(
        [
            str(python), "-m", "pip", "install",
            "--no-input", "--progress-bar", "off",
            "-r", str(req),
        ],
        app.path,
        PIP_TIMEOUT,
        on_line,
    )
    if code != 0:
        return False, "依赖安装失败：\n" + (out.strip()[-1200:] or f"pip 退出码 {code}")

    cfgmod.log(f"[env] {app.id} 依赖装好了")
    return True, ""


def ensure(app, note=lambda _text: None) -> tuple[bool, str]:
    """把「首次运行」整套做完。成功即代表 `app.python_exe()` 可用。"""
    ok, message = create(app, note)
    if not ok:
        return False, message

    ok, message = install_deps(app, note)
    if not ok:
        return False, message

    return True, ""
