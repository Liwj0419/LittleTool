"""无界面自检：检查配置、入口、依赖和本工具业务；不启动窗口或监听器。"""
from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
from pathlib import Path

from . import config as cfgmod


def run() -> int:
    checks = []
    try:
        cfg = cfgmod.Config.load()
        root = cfgmod.app_dir()
        data = json.loads(cfgmod.strip_jsonc((root / "config.json").read_text(encoding="utf-8-sig")))
        assert data.get("appName"), "缺少 appName"
        assert data.get("tool", {}).get("mode") == "resident", "tool.mode 不正确"
        assert Path(sys.prefix).resolve() == (root / ".venv").resolve(), "请使用本工具的 .venv"
        for name in ("app.py", "custom_instruct_start.bat", "run.sh", "requirements.txt", "assets/index.html"):
            assert (root / name).is_file(), "缺少 " + name
        checks.append("配置、工具形态、独立环境与启动入口")
        assert importlib.util.find_spec("webview"), "缺少 pywebview"
        from .store import load, normalize_command, resolve_path
        table = load(cfg)
        assert not table.errors, table.errors
        assert normalize_command("svn_u") == "svn update"
        assert Path(resolve_path("../demo", root)) == (root / "../demo").resolve()
        if table.entries:
            entry = table.entries[0]
            assert table.exact(entry.search_text).path == entry.path
        checks.append("指令解析、路径解析与精确匹配（不执行指令）")
        result = {"ok": True, "tool": "custom_instruct", "checks": checks}
    except Exception as exc:
        result = {"ok": False, "tool": "custom_instruct", "checks": checks, "error": repr(exc)}
    text = json.dumps(result, ensure_ascii=False, indent=2)
    (cfgmod.app_dir() / "selftest.log").write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0 if result["ok"] else 1
