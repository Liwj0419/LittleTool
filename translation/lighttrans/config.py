"""路径、日志、config.json（支持注释）、偏好与窗口位置。"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

APP_NAME = "LightTranslator"

DEFAULT_PROVIDERS = [
    {
        "id": "mymemory",
        "name": "MyMemory",
        "enabled": True,
        "url": "https://api.mymemory.translated.net/get",
    },
]

DEFAULT_SHORTCUTS = {
    "global": "Ctrl+Alt+2",
    "hide": "Esc",
    "focus": "Ctrl+F",
    "translate": "Ctrl+Enter",
    "clear": "Ctrl+L",
    "copy": "Ctrl+Shift+C",
}

# 首次运行时写出去的默认配置，带注释方便用户改
DEFAULT_CONFIG_TEXT = """{
  "tool": { "mode": "resident" },
  "appName": "轻翻译",
  // 窗口尺寸（只影响首次启动，之后会记住你调过的尺寸）
  "window": { "width": 874, "height": 941 },

  // 快捷键。留空、删掉整行或者注释掉，就表示不用这个快捷键。
  // 支持 Ctrl / Alt / Shift / Win 组合；键名可用字母、数字、Esc、Enter、F1-F12 等。
  "shortcuts": {
    "global":    "Ctrl+Alt+2",     // 全局唤出窗口（主键盘和小键盘的数字都行）
    "hide":      "Esc",            // 收起窗口
    "focus":     "Ctrl+F",         // 光标跳到输入框
    "translate": "Ctrl+Enter",     // 立即翻译
    "clear":     "Ctrl+L",         // 清空
    "copy":      "Ctrl+Shift+C"    // 复制译文
  },

  // 在线翻译接口，输入文字会发送给已启用的服务。
  "providers": [
    { "id": "mymemory", "name": "MyMemory", "enabled": true, "url": "https://api.mymemory.translated.net/get" }
  ]
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
            f.write(f"{datetime.now():%Y-%m-%d %H:%M:%S} [translation] {line}\n")
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
    width: int = 874
    height: int = 941
    shortcuts: dict[str, str] = field(default_factory=dict)
    providers: list = field(default_factory=lambda: [dict(p) for p in DEFAULT_PROVIDERS])

    @property
    def path(self) -> Path:
        return app_dir() / "config.json"

    def get(self, key: str) -> str | None:
        return self.shortcuts.get(key)

    def page(self) -> dict:
        """传给页面的那部分配置。"""
        return {"providers": self.providers, "shortcuts": self.shortcuts}

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

            # 快捷键不与默认值合并，否则被注释掉的项会「复活」
            sc = data.get("shortcuts")
            if isinstance(sc, dict):
                cfg.shortcuts = {k: v for k, v in sc.items() if isinstance(v, str)}

            pv = data.get("providers")
            if isinstance(pv, list):
                cfg.providers = pv
        except Exception as e:  # 文件存在但解析失败：保留原文件，用默认配置
            log(f"[config] 读取失败，使用默认配置：{e}")

        return cfg




# --------------------------------------------------------------------------- 窗口位置


def load_bounds() -> tuple[int, int, int, int] | None:
    try:
        data = json.loads((data_dir() / "bounds.json").read_text(encoding="utf-8"))
        x, y, w, h = (int(data[k]) for k in ("x", "y", "width", "height"))
        if w >= 320 and h >= 320:
            return x, y, w, h
    except (OSError, ValueError, KeyError):
        pass
    return None


def save_bounds(rect: tuple[int, int, int, int]) -> None:
    x, y, w, h = rect
    try:
        (data_dir() / "bounds.json").write_text(
            json.dumps({"x": x, "y": y, "width": w, "height": h}),
            encoding="utf-8",
        )
    except OSError:
        pass
