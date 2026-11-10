"""按 Manager 配置里的 `apps` 清单取工具；名称和能力来自各工具自己的配置。

只有写在 `apps` 里的工具才会出现：没登记的目录当作不存在，登记了但目录里
没有 `app.py` 的也当作不存在（两种都只写日志，不占列表）。列表顺序就是
`apps` 里的书写顺序。
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from . import config as cfgmod

SKIP_DIRS = {cfgmod.SELF_ID, "__pycache__"}
MODES = {"resident", "window", "background"}


@dataclass
class PlugApp:
    id: str
    cn: str
    path: Path
    config_path: Path
    hotkey: str
    openable: bool
    venv_ok: bool
    mode: str = "resident"
    config_error: str = ""

    @property
    def app_py(self) -> Path:
        return self.path / "app.py"

    def python_exe(self, console: bool = False) -> Path | None:
        names = ("python.exe", "pythonw.exe") if console else ("pythonw.exe", "python.exe")
        for relative in [*(Path("Scripts") / name for name in names), Path("bin/python")]:
            exe = self.path / ".venv" / relative
            if exe.is_file():
                return exe
        return None

    def page(self) -> dict:
        return {
            "id": self.id, "cn": self.cn, "en": self.id,
            "path": str(self.path), "config": str(self.config_path),
            "hotkey": self.hotkey, "openable": self.openable,
            "venvOk": self.venv_ok, "mode": self.mode,
            "configError": self.config_error,
        }


def _configuration(path: Path) -> tuple[dict, str]:
    try:
        data = json.loads(cfgmod.strip_jsonc(path.read_text(encoding="utf-8-sig")))
        if not isinstance(data, dict):
            raise ValueError("配置最外层必须是对象")
        return data, ""
    except (OSError, ValueError) as exc:
        return {}, f"配置读取失败：{exc}"


def _label(meta: dict, own: dict, app_id: str) -> str:
    for value in (meta.get("name"), own.get("appName")):
        if isinstance(value, str) and value.strip():
            return value.strip()
    return app_id


def discover(cfg: cfgmod.Config) -> list[PlugApp]:
    """按 `apps` 清单逐个核对目录；不存在的条目直接跳过。"""
    apps: list[PlugApp] = []
    for app_id, meta in cfg.apps.items():
        if (not app_id or app_id.startswith(".") or app_id in SKIP_DIRS
                or "/" in app_id or "\\" in app_id):
            cfgmod.log(f"[catalog] 忽略无效条目：{app_id!r}")
            continue
        folder = cfgmod.PLUG_ROOT / app_id
        if not (folder / "app.py").is_file():
            cfgmod.log(f"[catalog] 跳过 {app_id}：没有找到 {folder / 'app.py'}")
            continue

        own, error = _configuration(folder / "config.json")
        tool = own.get("tool", {})
        mode = tool.get("mode", "resident") if isinstance(tool, dict) else None
        if not isinstance(mode, str) or mode not in MODES:
            error = error or "tool.mode 必须是 resident、window 或 background"
            mode = "resident"
        shortcuts = own.get("shortcuts", {})
        hotkey = shortcuts.get("global", "") if isinstance(shortcuts, dict) else ""
        if isinstance(hotkey, list):
            hotkey = " / ".join(x.strip() for x in hotkey if isinstance(x, str) and x.strip())
        app = PlugApp(
            id=app_id, cn=_label(meta, own, app_id), path=folder,
            config_path=folder / "config.json",
            hotkey=hotkey.strip() if isinstance(hotkey, str) else "",
            openable=mode != "background",
            venv_ok=False, mode=mode, config_error=error,
        )
        app.venv_ok = app.python_exe(console=True) is not None
        apps.append(app)
    return apps
