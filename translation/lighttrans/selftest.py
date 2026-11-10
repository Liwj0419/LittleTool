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
        for name in ("app.py", "translation_start.bat", "run.sh", "requirements.txt", "assets/index.html"):
            assert (root / name).is_file(), "缺少 " + name
        checks.append("配置、工具形态、独立环境与启动入口")
        assert importlib.util.find_spec("webview"), "缺少 pywebview"
        from urllib.parse import urlparse
        providers = [p for p in data.get("providers", []) if p.get("enabled", True)]
        assert providers, "没有启用的翻译服务"
        for provider in providers:
            url = urlparse(provider["url"])
            assert url.scheme == "https" and url.netloc, "翻译地址无效"
            assert provider["id"] in ("tencent", "mymemory"), "不支持的翻译服务"
        checks.append("翻译服务配置（不发送外部请求）")
        result = {"ok": True, "tool": "translation", "checks": checks}
    except Exception as exc:
        result = {"ok": False, "tool": "translation", "checks": checks, "error": repr(exc)}
    text = json.dumps(result, ensure_ascii=False, indent=2)
    (cfgmod.app_dir() / "selftest.log").write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0 if result["ok"] else 1
