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
        for name in ("app.py", "excel_quick_search_start.bat", "run.sh", "requirements.txt", "assets/index.html"):
            assert (root / name).is_file(), "缺少 " + name
        checks.append("配置、工具形态、独立环境与启动入口")
        assert importlib.util.find_spec("webview"), "缺少 pywebview"
        from .scan import scan
        with tempfile.TemporaryDirectory(prefix="excel_quick_search_selftest_") as tmp:
            folder = Path(tmp)
            for name in ("正常.xlsx", "_win_skip.xlsx", "readme.txt"):
                (folder / name).write_text("sample", encoding="utf-8")
            result = scan({"key": "test", "name": "测试", "path": tmp},
                          {"extensions": [".xlsx"], "skip_prefix": ["_win_"]})
            assert result["ok"] and [f["name"] for f in result["files"]] == ["正常.xlsx"], result
            assert not scan({"path": str(folder / "missing")}, {})["ok"]
        checks.append("文件名扫描、前缀排除与缺失目录处理")
        result = {"ok": True, "tool": "excel_quick_search", "checks": checks}
    except Exception as exc:
        result = {"ok": False, "tool": "excel_quick_search", "checks": checks, "error": repr(exc)}
    text = json.dumps(result, ensure_ascii=False, indent=2)
    (cfgmod.app_dir() / "selftest.log").write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0 if result["ok"] else 1
