"""工具箱接入边界：临时目录、模拟进程，不启动真实工具或修改系统设置。"""
from __future__ import annotations

import json
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from manager import catalog, config, envsetup, processes


class ToolContractTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="任意工具箱名字 ")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.cfg = config.Config()
        self.root_patch = patch.object(config, "PLUG_ROOT", self.root)
        self.root_patch.start()
        self.addCleanup(self.root_patch.stop)

    def make_tool(self, name, mode="resident", own=None):
        folder = self.root / name
        folder.mkdir(parents=True)
        (folder / "app.py").write_text("", encoding="utf-8")
        data = own if own is not None else {"appName": "工具中文名", "tool": {"mode": mode}}
        (folder / "config.json").write_text(json.dumps(data), encoding="utf-8")
        return folder

    def test_only_configured_tools_are_listed(self):
        self.make_tool("listed_tool")
        self.make_tool("unlisted_tool")
        self.cfg.apps = {"listed_tool": {"name": "用户改名"}}
        found = catalog.discover(self.cfg)
        self.assertEqual([a.id for a in found], ["listed_tool"])
        self.assertEqual(found[0].cn, "用户改名")

    def test_list_order_follows_config(self):
        for name in ("alpha_tool", "beta_tool", "gamma_tool"):
            self.make_tool(name)
        self.cfg.apps = {
            "gamma_tool": {"name": "丙"},
            "alpha_tool": {"name": "甲"},
            "beta_tool": {"name": "乙"},
        }
        self.assertEqual([a.id for a in catalog.discover(self.cfg)],
                         ["gamma_tool", "alpha_tool", "beta_tool"])

    def test_configured_but_missing_folder_or_entry_point_is_hidden(self):
        self.make_tool("real_tool")
        (self.root / "no_entry").mkdir()  # 有目录但没 app.py
        self.cfg.apps = {
            "ghost_tool": {"name": "已删除"},
            "no_entry": {"name": "缺入口"},
            "real_tool": {"name": "真软件"},
        }
        self.assertEqual([a.id for a in catalog.discover(self.cfg)], ["real_tool"])

    def test_modes_and_name_fallback(self):
        for mode in ("resident", "window", "background"):
            self.make_tool(mode + "_tool", mode)
        self.cfg.apps = {
            "resident_tool": {},                     # 没写 name → 用工具自己的 appName
            "window_tool": {"name": "  "},           # 只有空白 → 仍用 appName
            "background_tool": {"name": "后台工具"},
        }
        found = {app.id: app for app in catalog.discover(self.cfg)}
        self.assertEqual(found["resident_tool"].cn, "工具中文名")
        self.assertEqual(found["window_tool"].cn, "工具中文名")
        self.assertTrue(found["window_tool"].openable)
        self.assertFalse(found["background_tool"].openable)
        self.assertEqual(found["resident_tool"].mode, "resident")

    def test_bad_configuration_remains_visible_with_reason(self):
        self.make_tool("broken_tool", own={"tool": {"mode": []}})
        self.cfg.apps = {"broken_tool": {"name": "坏配置"}}
        found = catalog.discover(self.cfg)
        self.assertEqual([a.id for a in found], ["broken_tool"])
        self.assertIn("tool.mode", found[0].config_error)
        (self.root / "broken_tool/config.json").write_text("{broken", encoding="utf-8")
        self.assertIn("配置读取失败", catalog.discover(self.cfg)[0].config_error)

    def test_invalid_app_ids_are_ignored(self):
        self.make_tool("manager")  # 不能把 Manager 自己或缓存目录当作工具
        self.make_tool("real_tool")
        self.cfg.apps = {
            "manager": {"name": "自己"},
            "__pycache__": {"name": "缓存"},
            ".hidden": {"name": "隐藏"},
            "nested/tool": {"name": "嵌套"},
            "real_tool": {"name": "真软件"},
        }
        self.assertEqual([a.id for a in catalog.discover(self.cfg)], ["real_tool"])

    def test_start_uses_tools_own_interpreter_and_mode_without_shim(self):
        for mode in ("resident", "window", "background"):
            folder = self.make_tool(mode + "_tool", mode)
            scripts = folder / ".venv/Scripts"
            scripts.mkdir(parents=True)
            (scripts / "python.exe").touch()
        self.cfg.apps = {name: {"name": name} for name in
                         ("resident_tool", "window_tool", "background_tool")}
        for app in catalog.discover(self.cfg):
            with patch.object(processes.subprocess, "Popen") as spawn:
                self.assertTrue(processes.start(app)[0])
                args, options = spawn.call_args
                self.assertEqual(Path(args[0][0]), app.path / ".venv/Scripts/python.exe")
                self.assertEqual(args[0][-1], "--show" if app.mode == "window" else "--hidden")
                self.assertEqual(options["cwd"], str(app.path))
                self.assertNotIn(str(config.APP_DIR / "shim"), options["env"].get("PYTHONPATH", ""))

    def test_process_tree_includes_runtime_children_but_excludes_independent_tools(self):
        folder = self.root / "first_tool"
        sibling = self.root / "second_tool"
        procs = [
            processes.ProcInfo(10, 1, str(folder / ".venv/Scripts/python.exe")),
            processes.ProcInfo(11, 10, "C:/Python/python.exe"),
            processes.ProcInfo(12, 11, "C:/Edge/msedgewebview2.exe"),
            processes.ProcInfo(13, 11, str(sibling / ".venv/Scripts/pythonw.exe")),
            processes.ProcInfo(14, 13, "C:/Python/pythonw.exe"),
            processes.ProcInfo(15, 11, "C:/Editor/Unity.exe"),
            processes.ProcInfo(16, 15, "C:/Editor/worker.exe"),
        ]
        self.assertEqual(processes.pids_of(folder, procs), [10, 11, 12])
        self.assertEqual(processes.pids_of(sibling, procs), [13, 14])

    def test_silent_environment_install_command_times_out(self):
        started = time.monotonic()
        code, message = envsetup._run([sys.executable, "-c", "import time; time.sleep(30)"],
                                     self.root, timeout=0.3)
        self.assertEqual(code, -1)
        self.assertIn("超时", message)
        self.assertLess(time.monotonic() - started, 4)

    def test_poll_rebuilds_list_in_new_config_order(self):
        """改了 config.json 的 apps 顺序，下一次轮询就把新顺序送回界面。"""
        from unittest.mock import patch as _patch

        from manager.core import App

        for name in ("alpha_tool", "beta_tool"):
            self.make_tool(name, own={"appName": name})
        app_dir = self.root / "manager_app"
        app_dir.mkdir()
        cfg_path = app_dir / "config.json"

        def write(order):
            cfg_path.write_text(
                json.dumps({"apps": {key: {"name": key} for key in order}}),
                encoding="utf-8",
            )

        write(["beta_tool", "alpha_tool"])
        with _patch.object(config, "APP_DIR", app_dir):
            app = App()
            try:
                self.assertEqual([a.id for a in app.apps], ["beta_tool", "alpha_tool"])
                self.assertNotIn("apps", app.poll())  # 配置没动，只回状态

                write(["alpha_tool", "beta_tool"])
                payload = app.poll()
                self.assertEqual([a["id"] for a in payload["apps"]],
                                 ["alpha_tool", "beta_tool"])
            finally:
                app.shutdown()


if __name__ == "__main__":
    unittest.main()
