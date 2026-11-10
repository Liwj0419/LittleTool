"""路径、日志、config.json（支持注释）。"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

APP_NAME = "CustomInstruct"

DEFAULT_SHORTCUTS = {
    "global": "Ctrl+Alt+1",
    "hide": "Esc",
    "run": "Enter",
    "next": "Down",
    "prev": "Up",
    "page_next": "PageDown",
    "page_prev": "PageUp",
    "reload": "F5",
    "edit": "Ctrl+O",
}

DEFAULT_BEHAVIOR = {
    "hide_on_blur": True,  # 点到别的地方就收起
    "always_on_top": True,  # 唤出时盖在最前面
    "beep_on_miss": True,  # 没匹配到任何指令时响一声
}

# 首次运行时写出去的默认配置，带注释方便用户改
DEFAULT_CONFIG_TEXT = """{
  "shortcuts": {
    "global": "Ctrl+Alt+1", // 全局唤起
    // 窗口内快捷键
    "hide": "Esc",
    "run": "Enter",
    "next": "Down",
    "prev": "Up",
    "page_next": "PageDown",
    "page_prev": "PageUp",
    "reload": "F5",
    "edit": "Ctrl+O"
  },

  // 指令列表，path 填要打开的文件或目录
  "commands": [
    // { "name": "用户文件夹", "command": "open", "path": "%USERPROFILE%" }
  ],

  // 其他设置
  "appName": "指令启动器",
  "tool": { "mode": "resident" },
  "window": { "width": 400, "height": 380 },
  "theme": "auto",
  "behavior": {
    "hide_on_blur": true,
    "always_on_top": true,
    "beep_on_miss": true
  }
}
"""


# --------------------------------------------------------------------------- 路径


def app_dir() -> Path:
    """程序所在目录（源码运行时就是项目根）。"""
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
            f.write(f"{datetime.now():%Y-%m-%d %H:%M:%S} [custom_instruct] {line}\n")
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
    width: int = 400
    height: int = 380
    theme: str = "auto"
    shortcuts: dict[str, str] = field(default_factory=dict)
    behavior: dict[str, bool] = field(default_factory=dict)
    commands: list[dict] = field(default_factory=list)

    @property
    def path(self) -> Path:
        return app_dir() / "config.json"

    def get(self, key: str) -> str | None:
        return self.shortcuts.get(key)

    def flag(self, key: str) -> bool:
        value = self.behavior.get(key)
        if isinstance(value, bool):
            return value
        return bool(DEFAULT_BEHAVIOR.get(key, False))

    def page(self) -> dict:
        """传给页面的那部分配置。"""
        return {
            "shortcuts": self.shortcuts,
            "behavior": self.behavior,
            "theme": self.theme,
            "window": {"width": self.width, "height": self.height},
        }

    @classmethod
    def load(cls) -> "Config":
        cfg = cls()
        cfg.shortcuts = dict(DEFAULT_SHORTCUTS)
        cfg.behavior = dict(DEFAULT_BEHAVIOR)

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

            theme = data.get("theme")
            if isinstance(theme, str) and theme.lower() in ("auto", "dark", "light"):
                cfg.theme = theme.lower()

            # 快捷键：写在文件里的项覆盖默认值，但没写的项仍用默认值
            sc = data.get("shortcuts")
            if isinstance(sc, dict):
                for key, value in sc.items():
                    if isinstance(value, str):
                        cfg.shortcuts[key] = value

            bh = data.get("behavior")
            if isinstance(bh, dict):
                for key, value in bh.items():
                    if isinstance(value, bool):
                        cfg.behavior[key] = value

            # 指令表：原样留着，交给 store 去逐条校验
            cmds = data.get("commands")
            if isinstance(cmds, list):
                cfg.commands = [item for item in cmds if isinstance(item, dict)]
        except Exception as e:  # 文件存在但解析失败：保留原文件，用默认配置
            log(f"[config] 读取失败，使用默认配置：{e}")

        return cfg
