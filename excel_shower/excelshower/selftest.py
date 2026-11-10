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
        for name in ("app.py", "excel_shower_start.bat", "run.sh", "requirements.txt", "assets/index.html"):
            assert (root / name).is_file(), "缺少 " + name
        checks.append("配置、工具形态、独立环境与启动入口")
        assert importlib.util.find_spec("webview"), "缺少 pywebview"
        from .sheet import load_table
        from .model import Viewer
        from .config import State
        with tempfile.TemporaryDirectory(prefix="excel_shower_selftest_") as tmp:
            path = Path(tmp) / "中文.csv"
            path.write_text("名称,数量\n猫,2\n狗,10\n", encoding="utf-8-sig")
            table = load_table(str(path))
            assert table.headers == ["名称", "数量"] and table.nrows == 2
            state = State()
            state.save = lambda: None
            viewer = Viewer(cfg, state)
            assert viewer.open(str(path)), viewer.error
            viewer.set_query("猫")
            assert len(viewer.view) == 1
            viewer.set_query("")
            assert len(viewer.view) == 2
        checks.append("中文 CSV 读取与搜索（临时状态，不改用户历史）")
        result = {"ok": True, "tool": "excel_shower", "checks": checks}
    except Exception as exc:
        result = {"ok": False, "tool": "excel_shower", "checks": checks, "error": repr(exc)}
    text = json.dumps(result, ensure_ascii=False, indent=2)
    (cfgmod.app_dir() / "selftest.log").write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0 if result["ok"] else 1
