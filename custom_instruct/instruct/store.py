"""把 config.json 里的 commands 解析成指令表。

一条指令长这样：{ "name": "用户文件夹", "command": "open", "path": "%USERPROFILE%" }
command 可以省略，省略等于 open；只有三种：open / svn commit / svn update。
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

COMMAND_OPEN = "open"
COMMAND_COMMIT = "svn commit"
COMMAND_UPDATE = "svn update"

# 写 command 时允许的别名（大小写、下划线、中划线都不计较）
_ALIASES = {
    "open": COMMAND_OPEN,
    "o": COMMAND_OPEN,
    "svn commit": COMMAND_COMMIT,
    "svn c": COMMAND_COMMIT,
    "commit": COMMAND_COMMIT,
    "svn update": COMMAND_UPDATE,
    "svn u": COMMAND_UPDATE,
    "update": COMMAND_UPDATE,
}


@dataclass
class Entry:
    index: int
    name: str
    command: str
    raw_path: str
    path: str

    @property
    def search_text(self) -> str:
        """启动器拿它做匹配："svn commit 项目" / "open 项目"，光打 "项目" 也能命中。"""
        return f"{self.command} {self.name}"

    @property
    def is_svn(self) -> bool:
        return self.command != COMMAND_OPEN

    def page(self) -> dict:
        return {
            "id": self.index,
            "name": self.name,
            "command": self.command,
            "search": self.search_text,
            "path": self.path,
        }


class Store:
    """一份解析好的指令表。每次唤出窗口都重新读，改完配置不用重启。"""

    def __init__(self, path: Path, entries: list[Entry], errors: list[str]) -> None:
        self.path = path
        self.entries = entries
        self.errors = errors

    def get(self, entry_id: int | None) -> Entry | None:
        if entry_id is None:
            return None
        for entry in self.entries:
            if entry.index == entry_id:
                return entry
        return None

    def exact(self, query: str) -> Entry | None:
        """整串完全相等的那一条（"svn commit 项目" 直接命中）。"""
        q = (query or "").strip().lower()
        if not q:
            return None
        for entry in self.entries:
            if entry.search_text.lower() == q:
                return entry
        return None


def load(cfg) -> Store:
    """从 Config 里取出 commands，逐条校验后拼成 Store。"""
    path = Path(cfg.path)
    base = path.parent

    entries: list[Entry] = []
    errors: list[str] = []

    for number, item in enumerate(cfg.commands, start=1):
        name = item.get("name")
        if not isinstance(name, str) or not name.strip():
            errors.append(f"第 {number} 条指令：缺少 name。")
            continue
        name = name.strip()

        raw_path = item.get("path")
        if not isinstance(raw_path, str) or not raw_path.strip():
            errors.append(f"第 {number} 条指令「{name}」：缺少 path。")
            continue
        raw_path = raw_path.strip()

        command = normalize_command(item.get("command"))
        if command is None:
            errors.append(
                f"第 {number} 条指令「{name}」：看不懂的 command {item.get('command')!r}，"
                "可用 open、svn commit、svn update。"
            )
            continue

        entry = Entry(
            index=len(entries),
            name=name,
            command=command,
            raw_path=raw_path,
            path=resolve_path(raw_path, base),
        )

        # 同一条指令写了两遍，多半是笔误：报出来，然后以后面的为准
        existing = next(
            (i for i, other in enumerate(entries)
             if other.search_text.lower() == entry.search_text.lower()),
            None,
        )
        if existing is not None:
            errors.append(
                f"第 {number} 条指令：'{entry.search_text}' 和前面重复了，以后面的为准。"
            )
            entry.index = entries[existing].index
            entries[existing] = entry
        else:
            entries.append(entry)

    # 去重之后重排 id，保证和页面里的下标一一对应
    for i, entry in enumerate(entries):
        entry.index = i

    return Store(path, entries, errors)


# --------------------------------------------------------------------------- 解析


def normalize_command(value: object) -> str | None:
    """把用户写的 command 归一成三种之一；省略时按 open 处理。"""
    if value is None:
        return COMMAND_OPEN
    if not isinstance(value, str):
        return None
    key = " ".join(value.strip().lower().replace("_", " ").replace("-", " ").split())
    return _ALIASES.get(key)


def _unquote(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
        return value[1:-1]
    return value


def resolve_path(raw_path: str, base: Path) -> str:
    """展开环境变量和 ~，相对路径按 config.json 所在目录算。"""
    path = _unquote(raw_path.strip())
    path = os.path.expandvars(path)
    if path.startswith("~"):
        path = os.path.expanduser(path)
    if not os.path.isabs(path):
        path = os.path.join(str(base), path)
    return os.path.normpath(path)
