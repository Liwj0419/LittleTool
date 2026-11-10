"""单实例：第二次启动不会开新窗口，而是把文件投给已经在跑的那个。

用 127.0.0.1 上的一个 TCP 端口做通信（顺便就是单实例锁）：
外部程序（比如 excel_quick_search）也能用同样的协议把文件投进来。
端口先按上次用过的、再按 config.json 里的 ``port`` 往后找。
"""

from __future__ import annotations

import socket
import threading

from . import config as cfgmod


# --------------------------------------------------------------------------- 客户端


def _ports() -> list[int]:
    ports: list[int] = []
    for p in (cfgmod.State.load().port, cfgmod.Config.load().port):
        if p and p not in ports:
            ports.append(p)
    return ports


def send(cmd: str, path: str | None = None, timeout: float = 0.6,
         ports: list[int] | None = None) -> bool:
    """给已运行的窗口发一条指令，成功返回 True。"""
    payload = cmd if path is None else f"{cmd}\t{path}"
    for port in (ports or _ports()):
        try:
            with socket.create_connection(("127.0.0.1", int(port)),
                                          timeout=timeout) as s:
                s.sendall((payload + "\n").encode("utf-8"))
                s.settimeout(timeout)
                return s.recv(8) == b"OK"
        except (OSError, ValueError):
            continue
    return False


def is_running() -> bool:
    return send("PING", timeout=0.25)


def launch(path: str | None = None) -> bool:
    """没有实例在跑：另起一个进程（不带控制台窗口）。"""
    import os
    import subprocess
    import sys

    app_dir = cfgmod.app_dir()
    exe = sys.executable
    if not exe.lower().endswith("pythonw.exe"):
        cand = os.path.join(os.path.dirname(exe), "pythonw.exe")
        if os.path.exists(cand):
            exe = cand
    cmd = [exe, str(app_dir / "app.py")]
    if path:
        cmd.append(os.path.abspath(path))
    try:
        subprocess.Popen(
            cmd, cwd=str(app_dir),
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        return True
    except OSError as e:
        cfgmod.log(f"[launch] {e!r}")
        return False


# --------------------------------------------------------------------------- 服务端


class Ipc:
    """在后台听端口：PING / OPEN <文件> / SHOW。"""

    def __init__(self, cfg, state, on_open, on_show) -> None:
        self.cfg = cfg
        self.state = state
        self.on_open = on_open
        self.on_show = on_show
        self.sock: socket.socket | None = None
        self.port = 0
        self._stop = threading.Event()

    def start(self) -> None:
        candidates = []
        if self.state.port:
            candidates.append(self.state.port)
        candidates += list(range(self.cfg.port, self.cfg.port + 30))

        for port in candidates:
            try:
                s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                s.bind(("127.0.0.1", port))
                s.listen(4)
            except OSError:
                try:
                    s.close()
                except Exception:
                    pass
                continue
            self.sock = s
            self.port = port
            self.state.port = port
            self.state.save()
            threading.Thread(target=self._loop, name="ipc", daemon=True).start()
            return
        cfgmod.log("[ipc] 没有可用端口，单实例投递不可用")

    def stop(self) -> None:
        self._stop.set()
        sock, self.sock = self.sock, None
        if sock is not None:
            try:
                sock.close()
            except OSError:
                pass

    def _loop(self) -> None:
        while not self._stop.is_set():
            sock = self.sock
            if sock is None:
                return
            try:
                conn, _ = sock.accept()
            except OSError:
                return
            try:
                with conn:
                    conn.settimeout(1.0)
                    data = conn.recv(8192).decode("utf-8", "replace").strip()
                    if not data:
                        continue
                    cmd, _, path = data.partition("\t")
                    if cmd == "PING":
                        conn.sendall(b"OK")
                    elif cmd == "OPEN" and path:
                        conn.sendall(b"OK")
                        self.on_show()
                        self.on_open(path)
                    elif cmd == "SHOW":
                        conn.sendall(b"OK")
                        self.on_show()
            except OSError:
                continue


def open_in_viewer(path: str) -> bool:
    """把文件交给查看器：已在运行就投过去，否则新开一个。"""
    import os

    path = os.path.abspath(path)
    from . import native

    native.allow_foreground()
    if send("OPEN", path):
        return True
    return launch(path)
