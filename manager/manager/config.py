"""路径、日志、config.json（支持注释）与各项默认值。

config.json 里只有"管理器自己"的偏好：窗口尺寸、快捷键、要展示哪些软件。
`apps` 同时是清单和中文名：只有写在这里的工具才会出现在列表里，
写了但找不到目录（没有 app.py）的一律不显示。
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

APP_NAME = "PlugManager"
SELF_ID = "manager"

APP_DIR = Path(__file__).resolve().parent.parent
PLUG_ROOT = APP_DIR.parent


def app_dir() -> Path:
    return APP_DIR


def assets_dir() -> Path:
    return APP_DIR / "assets"


def data_dir() -> Path:
    base = os.environ.get("APPDATA") or str(Path.home())
    d = Path(base) / APP_NAME
    d.mkdir(parents=True, exist_ok=True)
    return d


def log(line: str) -> None:
    try:
        with open(data_dir() / "app.log", "a", encoding="utf-8") as f:
            f.write(f"{datetime.now():%Y-%m-%d %H:%M:%S} [manager] {line}\n")
    except OSError:
        pass


def stamp(path: Path) -> tuple[int, int]:
    """配置文件指纹（修改时间 + 大小），用来判断要不要重读。"""
    try:
        info = path.stat()
    except OSError:
        return 0, 0
    return info.st_mtime_ns, info.st_size


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


def load_jsonc(path: Path) -> dict:
    """读一个允许注释的 JSON。读不动就返回空字典，绝不抛给调用方。"""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return {}
    try:
        data = json.loads(strip_jsonc(text))
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


# --------------------------------------------------------------------------- 默认配置

DEFAULT_CONFIG_TEXT = """{
  "shortcuts": {
    "global": "Ctrl+Alt+0", // 全局唤起
    // 窗口内快捷键
    "hide": "Esc"
  },

  // 工具清单，按这里的顺序显示
  "apps": {},

  // 其他设置
  "appName": "工具箱管理器",
  "tool": { "mode": "resident" },
  "window": { "width": 900, "height": 620, "minWidth": 740, "minHeight": 460 },
  "theme": "auto",
  "behavior": {
    "hideOnBlur": false,
    "pollMs": 1500,
    "closeToTray": true
  }
}
"""


# --------------------------------------------------------------------------- 配置


@dataclass
class Config:
    width: int = 900
    height: int = 620
    min_width: int = 740
    min_height: int = 460
    theme: str = "auto"
    shortcuts: dict[str, str] = field(default_factory=dict)
    hide_on_blur: bool = False
    poll_ms: int = 1500
    close_to_tray: bool = True
    apps: dict[str, dict] = field(default_factory=dict)

    @property
    def path(self) -> Path:
        return app_dir() / "config.json"

    def get(self, key: str) -> str | None:
        value = self.shortcuts.get(key)
        return value if isinstance(value, str) else None

    def app_meta(self, app_id: str) -> dict:
        meta = self.apps.get(app_id)
        return meta if isinstance(meta, dict) else {}

    @classmethod
    def load(cls) -> "Config":
        cfg = cls()
        path = cfg.path
        if not path.exists():
            try:
                path.write_text(DEFAULT_CONFIG_TEXT, encoding="utf-8")
            except OSError:
                pass
            data = load_jsonc(path)
        else:
            data = load_jsonc(path)

        win = data.get("window")
        if isinstance(win, dict):
            for key, attr in (
                ("width", "width"),
                ("height", "height"),
                ("minWidth", "min_width"),
                ("minHeight", "min_height"),
            ):
                value = win.get(key)
                if isinstance(value, int) and value > 200:
                    setattr(cfg, attr, value)

        if isinstance(data.get("theme"), str):
            cfg.theme = data["theme"]

        sc = data.get("shortcuts")
        if isinstance(sc, dict):
            cfg.shortcuts = {k: v for k, v in sc.items() if isinstance(v, str)}

        bh = data.get("behavior")
        if isinstance(bh, dict):
            if isinstance(bh.get("hideOnBlur"), bool):
                cfg.hide_on_blur = bh["hideOnBlur"]
            if isinstance(bh.get("pollMs"), int) and 300 <= bh["pollMs"] <= 10000:
                cfg.poll_ms = bh["pollMs"]
            if isinstance(bh.get("closeToTray"), bool):
                cfg.close_to_tray = bh["closeToTray"]

        if isinstance(data.get("apps"), dict):
            cfg.apps = {k: v for k, v in data["apps"].items()
                        if isinstance(k, str) and isinstance(v, dict)}

        return cfg
