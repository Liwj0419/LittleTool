"""路径、日志、config.json（支持 // 与 /* */ 注释）。"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

APP_NAME = "ExcelQuickSearch"

DEFAULT_SHORTCUTS = {
    "global": ["Ctrl+Alt+.", "Ctrl+Alt+>"],
    "hide": "Esc",
    "focus": "Ctrl+F",
    "open": "Enter",
    "reveal": "Ctrl+Enter",
    "copy": "Ctrl+Shift+C",
    "reload": "F5",
}

DEFAULT_SEARCH = {
    "extensions": [".xlsx", ".xls", ".xlsm", ".xlsb"],
    "skip_prefix": ["_win_"],
    "max_results": 5000,
    "max_depth": 0,
}

DEFAULT_OPEN = {"prefer_shower": True, "shower_dir": "../excel_shower"}

# 首次运行时写出去的默认配置，带注释方便用户改
DEFAULT_CONFIG_TEXT = r"""{
  "tool": { "mode": "resident" },
  "appName": "Excel 快速搜索",
  // 窗口尺寸（逻辑像素，会按显示器缩放换算）。最小 560x380。
  "window": {
    "width": 780,
    "height": 540,
    "theme": "dark",      // dark / light / auto（跟随系统）
    "on_top": true        // 始终置顶（和旧版一致），嫌挡事可以改 false
  },

  // 快捷键。留空、删掉整行或者注释掉，就表示不用这个快捷键。
  // 支持 Ctrl / Alt / Shift / Win 组合；键名可用字母、数字、. , ; / 等符号、Esc、Enter、F1-F12。
  "shortcuts": {
    "global": ["Ctrl+Alt+.", "Ctrl+Alt+>"],  // 全局唤起（可写一个字符串，也可以写数组挂多个）
    "hide":   "Esc",                         // 收起窗口（挂到托盘，进程继续待命）
    "focus":  "Ctrl+F",                      // 光标跳回搜索框
    "open":   "Enter",                       // 打开选中的文件
    "reveal": "Ctrl+Enter",                  // 在资源管理器里定位该文件
    "copy":   "Ctrl+Shift+C",                // 复制文件路径
    "reload": "F5"                           // 重新扫描当前数据源
  },

  // 把 path 填成自己的表格文件夹或一个表格文件，例如 "C:/表格"。
  // 只配一个路径时，打开窗口会直接扫描该位置。
  "sources": [
    { "key": "1", "name": "我的表格", "path": "" }
  ],

  // 扫描规则
  "search": {
    "extensions": [".xlsx", ".xls", ".xlsm", ".xlsb"],
    "skip_prefix": ["_win_"],   // 文件名以这些开头的直接跳过
    "max_results": 5000,        // 最多收多少个文件
    "max_depth": 0              // 子目录递归深度，0 = 不限
  },

  // 打开方式：优先交给同级的 excel_shower 查看器，找不到就退回系统默认程序
  "open": {
    "prefer_shower": true,
    "shower_dir": "../excel_shower"
  }
}
"""


# --------------------------------------------------------------------------- 路径


def app_dir() -> Path:
    return Path(__file__).resolve().parent.parent


def assets_dir() -> Path:
    return app_dir() / "assets"


def data_dir() -> Path:
    base = os.environ.get("APPDATA") or str(Path.home())
    d = Path(base) / APP_NAME
    d.mkdir(parents=True, exist_ok=True)
    return d


def log(line: str) -> None:
    try:
        with open(data_dir() / "app.log", "a", encoding="utf-8") as f:
            f.write(f"{datetime.now():%Y-%m-%d %H:%M:%S} [excel_quick_search] {line}\n")
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
    width: int = 780
    height: int = 540
    theme: str = "dark"
    on_top: bool = True
    shortcuts: dict[str, object] = field(default_factory=dict)
    sources: list = field(default_factory=list)
    search: dict = field(default_factory=lambda: dict(DEFAULT_SEARCH))
    open: dict = field(default_factory=lambda: dict(DEFAULT_OPEN))

    @property
    def path(self) -> Path:
        return app_dir() / "config.json"

    # ---------------------------------------------------------------- 取值

    def get(self, key: str) -> str | None:
        value = self.shortcuts.get(key)
        return value if isinstance(value, str) else None

    def global_specs(self) -> list[str]:
        """全局热键可以有多个，统一成列表。"""
        raw = self.shortcuts.get("global")
        if isinstance(raw, str):
            return [raw] if raw.strip() else []
        if isinstance(raw, list):
            return [s for s in raw if isinstance(s, str) and s.strip()]
        return []

    def source(self, key: str) -> dict | None:
        for item in self.sources:
            if str(item.get("key", "")) == str(key):
                return item
        return None

    def first_source(self) -> dict | None:
        return self.sources[0] if self.sources else None

    def window_on_top(self) -> bool:
        return bool(self.on_top)

    def page(self) -> dict:
        """传给页面的那部分配置。"""
        return {
            "theme": self.theme,
            "shortcuts": {k: v for k, v in self.shortcuts.items() if k != "global"}
            | {"global": ", ".join(self.global_specs())},
            "sources": [
                {
                    "key": str(s.get("key", "")),
                    "name": str(s.get("name", "") or s.get("path", "")),
                    "path": str(s.get("path", "")),
                }
                for s in self.sources
                if s.get("key") and s.get("path")
            ],
        }

    # ---------------------------------------------------------------- 读取

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
                    cfg.width = max(560, win["width"])
                if isinstance(win.get("height"), int):
                    cfg.height = max(380, win["height"])
                if win.get("theme") in ("dark", "light", "auto"):
                    cfg.theme = win["theme"]
                if isinstance(win.get("on_top"), bool):
                    cfg.on_top = win["on_top"]

            # 快捷键不与默认值合并，否则被注释掉的项会「复活」
            sc = data.get("shortcuts")
            if isinstance(sc, dict):
                cfg.shortcuts = {
                    k: v
                    for k, v in sc.items()
                    if isinstance(v, str) or (k == "global" and isinstance(v, list))
                }

            src = data.get("sources")
            if isinstance(src, list):
                cfg.sources = [
                    s
                    for s in src
                    if isinstance(s, dict) and s.get("key") and s.get("path")
                ]

            se = data.get("search")
            if isinstance(se, dict):
                cfg.search = {**DEFAULT_SEARCH, **se}

            op = data.get("open")
            if isinstance(op, dict):
                cfg.open = {**DEFAULT_OPEN, **op}
        except Exception as e:  # 文件存在但解析失败：保留原文件，用默认配置
            log(f"[config] 读取失败，使用默认配置：{e}")

        return cfg
