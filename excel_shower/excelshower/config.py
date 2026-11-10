"""路径、日志、config.json（支持注释）、运行状态与窗口位置。

配置分两处：

* ``config.json``（项目根目录）—— 用户手改的配置：窗口尺寸、快捷键、端口、
  行数上限。带注释，程序**只读不写**，所以注释永远丢不了。
* ``%LOCALAPPDATA%\\ExcelShower\\state.json`` —— 程序自己记的东西：上次窗口位置、
  最近打开的文件、实际用的端口。这些不该混进用户手写的配置里。
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

APP_NAME = "Excel 数据查看器"
APP_ID = "ExcelShower"

# 单表最多读取行数，防止误开巨型文件卡死
DEFAULT_MAX_ROWS = 200_000
DEFAULT_PAGE_ROWS = 2000
DEFAULT_MAX_RECENT = 8
DEFAULT_PORT = 47653

DEFAULT_SHORTCUTS = {
    "global": "Ctrl+Alt+E",
    "hide": "Esc",
    "open": "Ctrl+O",
    "search": "Ctrl+F",
    "reload": "F5",
    "selectAll": "Ctrl+A",
    "copy": "Ctrl+C",
}

# 首次运行时写出去的默认配置，带注释方便用户改
DEFAULT_CONFIG_TEXT = """{
  "tool": { "mode": "resident" },
  "appName": "Excel 数据查看器",
  // 窗口尺寸（只影响首次启动，之后会记住你调过的尺寸）
  "window": { "width": 1100, "height": 680, "minWidth": 820, "minHeight": 520 },

  // 快捷键。留空、删掉整行或者注释掉，就表示不用这个快捷键。
  // 支持 Ctrl / Alt / Shift / Win 组合；键名可用字母、数字、Esc、Enter、
  // F1-F12、Space、Tab、Up / Down / Left / Right、Home / End / PageUp / PageDown 等。
  "shortcuts": {
    "global":    "Ctrl+Alt+E",     // 全局唤出窗口（任何程序在前台都有效）
    "hide":      "Esc",            // 收起窗口
    "open":      "Ctrl+O",         // 选择文件
    "search":    "Ctrl+F",         // 光标跳到搜索框
    "reload":    "F5",             // 重新读取当前文件
    "selectAll": "Ctrl+A",         // 全选整张表
    "copy":      "Ctrl+C"          // 复制框选的区域
  },

  // 按 Esc 的行为：
  //   "close"   = 直接收进托盘（不管当前有没有选区 / 搜索 / 弹窗）
  //   "layered" = 一层一层退：弹窗 -> 下拉 -> 选区 -> 清搜索 -> 收进托盘
  "esc": "close",

  // 单实例通信端口（被占用时自动往后找）。别的程序靠它把文件投进这个窗口。
  "port": 47653,

  // 大文件保护：单表最多读多少行、每次往页面送多少行
  "maxRows": 200000,
  "pageRows": 2000,

  // 「历史」菜单最多记几条
  "maxRecent": 8
}
"""


# --------------------------------------------------------------------------- 路径


def app_dir() -> Path:
    """程序目录（源码运行时就是项目根）。"""
    return Path(__file__).resolve().parent.parent


def assets_dir() -> Path:
    return app_dir() / "assets"


def data_dir() -> Path:
    """程序自己的状态目录，不污染项目目录。"""
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or str(Path.home())
    elif sys.platform == "darwin":
        base = str(Path.home() / "Library" / "Application Support")
    else:
        base = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    d = Path(base) / APP_ID
    try:
        d.mkdir(parents=True, exist_ok=True)
    except OSError:
        return app_dir()
    return d


def log(line: str) -> None:
    try:
        with open(data_dir() / "app.log", "a", encoding="utf-8") as f:
            f.write(f"{datetime.now():%Y-%m-%d %H:%M:%S} [excel_shower] {line}\n")
    except OSError:
        pass


# --------------------------------------------------------------------------- JSONC


def strip_jsonc(text: str) -> str:
    """去掉 // 与 /* */ 注释，以及注释掉整行后残留的尾逗号。"""
    out: list[str] = []
    i, n = 0, len(text)
    in_str = False

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

        if c == "/" and i + 1 < n:
            nxt = text[i + 1]
            if nxt == "/":
                i += 2
                while i < n and text[i] not in "\r\n":
                    i += 1
                continue
            if nxt == "*":
                i += 2
                while i + 1 < n and not (text[i] == "*" and text[i + 1] == "/"):
                    i += 1
                i = min(i + 2, n)
                continue

        if c == ",":
            j = i + 1
            while j < n and text[j] in " \t\r\n":
                j += 1
            if j < n and text[j] in "}]":
                i += 1
                continue

        out.append(c)
        i += 1

    return "".join(out)


# --------------------------------------------------------------------------- 配置


@dataclass
class Config:
    width: int = 1100
    height: int = 680
    min_width: int = 820
    min_height: int = 520
    shortcuts: dict = field(default_factory=lambda: dict(DEFAULT_SHORTCUTS))
    esc: str = "close"
    port: int = DEFAULT_PORT
    max_rows: int = DEFAULT_MAX_ROWS
    page_rows: int = DEFAULT_PAGE_ROWS
    max_recent: int = DEFAULT_MAX_RECENT

    @property
    def path(self) -> Path:
        return app_dir() / "config.json"

    def get(self, key: str) -> str | None:
        """取一个快捷键写法，没配就是 None。"""
        value = self.shortcuts.get(key)
        return value if isinstance(value, str) and value.strip() else None

    def page(self) -> dict:
        """传给页面的那部分配置。"""
        return {
            "shortcuts": dict(self.shortcuts),
            "esc": self.esc,
            "app": APP_NAME,
        }

    @classmethod
    def load(cls) -> "Config":
        cfg = cls()
        path = cfg.path
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            # 文件不存在：写一份带注释的默认配置出去
            try:
                path.write_text(DEFAULT_CONFIG_TEXT, encoding="utf-8")
            except OSError:
                pass
            return cfg

        try:
            data = json.loads(strip_jsonc(text))
            if not isinstance(data, dict):
                raise ValueError("根节点不是对象")

            win = data.get("window")
            if isinstance(win, dict):
                if isinstance(win.get("width"), int):
                    cfg.width = win["width"]
                if isinstance(win.get("height"), int):
                    cfg.height = win["height"]
                if isinstance(win.get("minWidth"), int):
                    cfg.min_width = win["minWidth"]
                if isinstance(win.get("minHeight"), int):
                    cfg.min_height = win["minHeight"]

            # 快捷键不与默认值合并，否则被注释掉的项会「复活」
            sc = data.get("shortcuts")
            if isinstance(sc, dict):
                cfg.shortcuts = {k: v for k, v in sc.items() if isinstance(v, str)}

            if data.get("esc") in ("layered", "close"):
                cfg.esc = data["esc"]
            if isinstance(data.get("port"), int):
                cfg.port = data["port"]
            if isinstance(data.get("maxRows"), int) and data["maxRows"] > 0:
                cfg.max_rows = data["maxRows"]
            if isinstance(data.get("pageRows"), int) and data["pageRows"] > 0:
                cfg.page_rows = data["pageRows"]
            if isinstance(data.get("maxRecent"), int) and data["maxRecent"] >= 0:
                cfg.max_recent = data["maxRecent"]
        except Exception as e:  # 文件存在但解析失败：保留原文件，用默认配置
            log(f"[config] 读取失败，使用默认配置：{e}")

        return cfg


# --------------------------------------------------------------------------- 运行状态


@dataclass
class State:
    """程序自己记的东西（不写进用户手写的 config.json）。"""

    port: int = 0
    recent: list = field(default_factory=list)
    bounds: tuple | None = None

    @property
    def path(self) -> Path:
        return data_dir() / "state.json"

    @classmethod
    def load(cls) -> "State":
        st = cls()
        try:
            data = json.loads(cls().path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return st

        if isinstance(data.get("port"), int):
            st.port = data["port"]
        recent = data.get("recent")
        if isinstance(recent, list):
            st.recent = [p for p in recent if isinstance(p, str)]

        b = data.get("bounds")
        if isinstance(b, dict):
            try:
                x, y, w, h = (int(b[k]) for k in ("x", "y", "width", "height"))
                if w >= 600 and h >= 400:
                    st.bounds = (x, y, w, h)
            except (KeyError, TypeError, ValueError):
                pass
        return st

    def save(self) -> None:
        payload = {"port": self.port, "recent": self.recent}
        if self.bounds:
            x, y, w, h = self.bounds
            payload["bounds"] = {"x": x, "y": y, "width": w, "height": h}
        try:
            self.path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except OSError:
            pass
