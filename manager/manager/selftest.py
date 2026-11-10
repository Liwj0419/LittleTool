"""无界面自检：不开窗口，只把「配置了谁、扫到了谁、谁在跑」写进 selftest.log。

    python app.py --selftest
"""

from __future__ import annotations

import json
from datetime import datetime

from . import catalog, config as cfgmod, processes, webview2


def collect() -> dict:
    cfg = cfgmod.Config.load()
    apps = catalog.discover(cfg)
    procs = processes.snapshot()

    rows = []
    for app in apps:
        pids = processes.pids_of(app.path, procs)
        window = ""
        for pid in pids:
            window = processes.visible_window(pid)
            if window:
                break
        rows.append(
            {
                "id": app.id,
                "cn": app.cn,
                "hotkey": app.hotkey,
                "venv": app.venv_ok,
                "running": bool(pids),
                "window": window,
                "pids": pids,
                "config": str(app.config_path),
                "mode": app.mode,
                "openable": app.openable,
                "error": app.config_error,
            }
        )

    return {
        "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "root": str(cfgmod.PLUG_ROOT),
        "configured": sorted(cfg.apps),
        "count": len(rows),
        "missing": sorted(set(cfg.apps) - {row["id"] for row in rows}),
        "apps": rows,
        "webview2": webview2.version(),
    }


def run() -> int:
    data = collect()
    text = json.dumps(data, ensure_ascii=False, indent=2)

    try:
        (cfgmod.app_dir() / "selftest.log").write_text(text + "\n", encoding="utf-8")
    except OSError:
        pass

    runtime = data.get("webview2") or ""
    print(f"WebView2 运行时：{runtime or '未找到（界面无法启动，见 README）'}")
    print(f"配置 {len(data['configured'])} 个，展示 {data['count']} 个（{data['root']}）")
    for row in data["apps"]:
        mark = "运行中" if row["running"] else "已停止"
        window = f" 窗口：{row['window']}" if row["window"] else ""
        print(f"  - {row['cn']:<8} {row['id']:<18} [{row['mode']}] {mark}{window}")
        if row["error"]:
            print(f"    {row['error']}")
    if data["missing"]:
        print(f"配置了但没找到 app.py，已跳过：{', '.join(data['missing'])}")
    return 1 if (not runtime) or any(row["error"] for row in data["apps"]) else 0
