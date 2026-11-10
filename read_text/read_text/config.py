"""配置读取、路径、日志。

config.json 允许 // 与 /* */ 注释，解析前先剥掉。
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from datetime import datetime
from pathlib import Path

APP_NAME = "read_text"
APP_TITLE = "read_text"

DEFAULT_CONFIG_TEXT = """{
  "shortcuts": {
    "global": "Alt+F1", // 全局唤起
    // 窗口内快捷键
    "cancel": "Esc",
    "copy": "Ctrl+C",
    "all": "Ctrl+A"
  },

  // 其他设置
  "appName": "屏幕取字",
  "tool": { "mode": "background" },
  "overlay": {
    "selectionColor": "rgba(0,120,215,0.32)",
    "hintColor": "rgba(20,20,20,0.82)",
    "showHint": true,
    "hintText": "拖动选择文字，按 Ctrl+C 复制；Esc 退出"
  },

  // 文字识别参数，upscale 可设为 1 至 3
  "ocr": {
    "upscale": 2,
    "joinGap": 0.28
  },
  "behavior": {
    "copyOnRelease": false,
    "closeOnOutsideClick": false,
    "maxAreaRatio": 0.9
  }
}
"""


# --------------------------------------------------------------------------- 路径


def app_dir() -> Path:
    return Path(__file__).resolve().parent.parent


def assets_dir() -> Path:
    return app_dir() / "assets"


def data_dir() -> Path:
    if os.name == "nt":
        base = Path(os.environ.get("APPDATA") or Path.home()) / APP_NAME
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support" / APP_NAME
    else:
        base = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share") / APP_NAME
    try:
        base.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    return base


def temp_dir() -> Path:
    """交给 OCR 桥的临时图片放这儿，**不要**放 data_dir()。

    data_dir() 在 %APPDATA%，某些环境里这个目录的写入会被重定向/沙箱化：
    写它的进程（python）自己看得见，别的进程（桥进程 = 另一个 powershell）
    打开时却是「文件不存在」，于是识别永远失败。实测 %TEMP% 不受这个影响。
    """
    return Path(tempfile.gettempdir())


def log(line: str) -> None:
    try:
        with open(data_dir() / "app.log", "a", encoding="utf-8") as f:
            f.write("%s [read_text] %s\n" % (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), line))
    except OSError:
        pass


# --------------------------------------------------------------------------- JSONC


def strip_jsonc(text: str) -> str:
    """去掉 // 与 /* */ 注释，以及注释整行后残留的尾逗号。"""
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
        if c == "/" and i + 1 < n and text[i + 1] == "/":
            while i < n and text[i] not in "\r\n":
                i += 1
            continue
        if c == "/" and i + 1 < n and text[i + 1] == "*":
            i += 2
            while i + 1 < n and not (text[i] == "*" and text[i + 1] == "/"):
                i += 1
            i += 2
            continue
        out.append(c)
        i += 1

    cleaned = "".join(out)
    # 收拾 "值,\n}" 这种尾逗号
    result: list[str] = []
    i, n = 0, len(cleaned)
    in_str = False
    while i < n:
        c = cleaned[i]
        if in_str:
            result.append(c)
            if c == "\\" and i + 1 < n:
                result.append(cleaned[i + 1])
                i += 2
                continue
            if c == '"':
                in_str = False
            i += 1
            continue
        if c == '"':
            in_str = True
            result.append(c)
            i += 1
            continue
        if c == ",":
            j = i + 1
            while j < n and cleaned[j] in " \t\r\n":
                j += 1
            if j < n and cleaned[j] in "}]":
                i += 1
                continue
        result.append(c)
        i += 1
    return "".join(result)


# --------------------------------------------------------------------------- 配置


class Config:
    def __init__(self, data: dict | None = None) -> None:
        self.data = data or {}

    @property
    def path(self) -> Path:
        return app_dir() / "config.json"

    @property
    def shortcuts(self) -> dict:
        sc = self.data.get("shortcuts")
        return sc if isinstance(sc, dict) else {}

    def get(self, key: str, default=None):
        """优先当快捷键名查，再按 a.b.c 的点路径查，都没有就返回 default。"""
        if key in self.shortcuts:
            value = self.shortcuts[key]
            return value if value else default
        node = self.data
        for part in key.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return default if node is None else node

    def hotkey(self) -> str:
        return str(self.get("global", "") or "").strip()

    def page(self) -> dict:
        """发给页面的那部分配置。"""
        return {
            "selectionColor": self.get("overlay.selectionColor"),
            "hintColor": self.get("overlay.hintColor"),
            "showHint": bool(self.get("overlay.showHint", True)),
            "hintText": self.get("overlay.hintText"),
            "joinGap": float(self.get("ocr.joinGap", 0.28) or 0.28),
            "copyOnRelease": bool(self.get("behavior.copyOnRelease", False)),
            "closeOnOutsideClick": bool(self.get("behavior.closeOnOutsideClick", False)),
            "cancel": self.get("cancel", "Esc") or "Esc",
            "copy": self.get("copy", "Ctrl+C") or "Ctrl+C",
            "all": self.get("all", "Ctrl+A") or "Ctrl+A",
        }

    @classmethod
    def load(cls) -> "Config":
        path = app_dir() / "config.json"
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            try:
                path.write_text(DEFAULT_CONFIG_TEXT, encoding="utf-8")
            except OSError:
                pass
            return cls(json.loads(strip_jsonc(DEFAULT_CONFIG_TEXT)))

        try:
            data = json.loads(strip_jsonc(text))
            if not isinstance(data, dict):
                raise ValueError("根节点不是对象")
            return cls(data)
        except Exception as e:  # noqa: BLE001
            log("[config] 解析失败，用默认值：%r" % (e,))
            return cls(json.loads(strip_jsonc(DEFAULT_CONFIG_TEXT)))
