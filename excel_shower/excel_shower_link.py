# -*- coding: utf-8 -*-
"""Excel 数据查看器 —— 外部程序调用接口

给 excel_quick_search 之类的工具用：把文件丢给已经开着的查看器窗口，
没开着就顺手启动一个。

用法::

    import excel_shower_link
    excel_shower_link.open_file(r"C:\\表格\\示例.xlsx")

这个模块故意只依赖标准库：加载快，也不会把 GUI 拖进调用方进程。
端口先看查看器自己记的 state.json，再看 config.json 里的 ``port``。
"""

import json
import os
import socket
import subprocess
import sys

APP_DIR = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(APP_DIR, "app.py")


def _data_dir():
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    elif sys.platform == "darwin":
        base = os.path.join(os.path.expanduser("~"), "Library", "Application Support")
    else:
        base = os.environ.get("XDG_DATA_HOME") or os.path.join(
            os.path.expanduser("~"), ".local", "share")
    return os.path.join(base, "ExcelShower")


def _read_json(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f) or {}
    except (OSError, ValueError):
        return {}


def _strip_jsonc(text):
    """config.json 里带注释，读端口前先去掉。"""
    out, i, n, in_str = [], 0, len(text), False
    while i < n:
        c = text[i]
        if in_str:
            out.append(c)
            if c == "\\" and i + 1 < n:
                out.append(text[i + 1])
                i += 2
                continue
            if c == '"':
                in_str = False
            i += 1
            continue
        if c == '"':
            in_str = True
            out.append(c)
            i += 1
            continue
        if c == "/" and i + 1 < n and text[i + 1] in "/*":
            if text[i + 1] == "/":
                i += 2
                while i < n and text[i] not in "\r\n":
                    i += 1
            else:
                i += 2
                while i + 1 < n and not (text[i] == "*" and text[i + 1] == "/"):
                    i += 1
                i = min(i + 2, n)
            continue
        out.append(c)
        i += 1
    return "".join(out)


def _config_port():
    try:
        with open(os.path.join(APP_DIR, "config.json"), "r", encoding="utf-8") as f:
            data = json.loads(_strip_jsonc(f.read())) or {}
        port = data.get("port")
        return int(port) if isinstance(port, int) else 47653
    except (OSError, ValueError):
        return 47653


def _ports():
    ports = []
    for port in (_read_json(os.path.join(_data_dir(), "state.json")).get("port"),
                 _config_port()):
        if isinstance(port, int) and port and port not in ports:
            ports.append(port)
    return ports


def send(cmd, path=None, timeout=0.5):
    """给查看器发一条指令。成功返回 True，没在运行返回 False。"""
    payload = cmd if path is None else "%s\t%s" % (cmd, path)
    for port in _ports():
        try:
            with socket.create_connection(("127.0.0.1", int(port)),
                                          timeout=timeout) as s:
                s.sendall((payload + "\n").encode("utf-8"))
                s.settimeout(timeout)
                return s.recv(8) == b"OK"
        except (OSError, ValueError):
            continue
    return False


def is_running():
    return send("PING", timeout=0.25)


def _pythonw():
    for relative in (".venv/Scripts/pythonw.exe", ".venv/Scripts/python.exe", ".venv/bin/python"):
        exe = os.path.join(APP_DIR, relative)
        if os.path.isfile(exe):
            return exe
    return None


def allow_foreground():
    """把"可以抢前台"的权限让给查看器窗口。

    这一下是调用方（quick_search）收到了用户输入，Windows 只认它；
    查看器自己 SetForegroundWindow 会被拒，得先松口（ASFW_ANY）。
    """
    try:
        import ctypes
        u = ctypes.WinDLL("user32", use_last_error=True)
        u.AllowSetForegroundWindow.argtypes = [ctypes.c_uint]
        u.AllowSetForegroundWindow(0xFFFFFFFF)
    except Exception:
        pass


def open_file(path):
    """在 Excel 数据查看器里打开 path。返回 True 表示已处理。"""
    if not path or not os.path.exists(path):
        return False
    path = os.path.abspath(path)
    allow_foreground()
    if send("OPEN", path):
        return True
    python = _pythonw()
    if python is None:
        return False
    try:
        subprocess.Popen([python, SCRIPT, path], cwd=APP_DIR,
                         creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        return True
    except OSError:
        return False


def show_window():
    """把已经缩到托盘的窗口叫出来。"""
    allow_foreground()
    return send("SHOW")
