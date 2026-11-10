"""公开 bat 入口：在改名后的路径中透传参数，并返回命令行操作的失败码。"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


@unittest.skipUnless(os.name == "nt", "Windows bat contract")
class ConsoleLaunchersTest(unittest.TestCase):
    def test_selftest_returns_exit_code_and_preserves_arguments(self):
        toolbox = Path(__file__).resolve().parents[2]
        for tool in sorted(toolbox.iterdir()):
            if not (tool / "app.py").is_file():
                continue
            with self.subTest(tool=tool.name), tempfile.TemporaryDirectory(
                prefix="任意名字 %literal% & space "
            ) as temporary:
                folder = Path(temporary)
                source = (tool / f"{tool.name}_start.bat").read_text(encoding="ascii")
                # Use the test's installed environment only for this temporary fixture.
                # The real launcher still selects the target tool's relative .venv.
                source = source.replace('set "PY=.venv\\Scripts\\python.exe"',
                                        f'set "PY={sys.executable}"')
                source = source.replace('set "PYW=.venv\\Scripts\\pythonw.exe"',
                                        f'set "PYW={Path(sys.executable).with_name("pythonw.exe")}"')
                bat = folder / "fixture_start.bat"
                bat.write_text(source, encoding="utf-8", newline="\n")
                (folder / "app.py").write_text(
                    "import json,sys\nfrom pathlib import Path\n"
                    "Path(__file__).with_name('args.json').write_text(json.dumps(sys.argv[1:]), encoding='utf-8')\n"
                    "raise SystemExit(27)\n", encoding="utf-8"
                )
                expected = ["", "--hidden", "--selftest", "--show", "quoted file & name.csv"]
                result = subprocess.run(
                    [os.environ.get("COMSPEC", "cmd.exe"), "/d", "/c", bat.name, *expected],
                    cwd=folder, capture_output=True, timeout=15,
                )
                self.assertEqual(result.returncode, 27, result.stdout + result.stderr)
                self.assertEqual(json.loads((folder / "args.json").read_text(encoding="utf-8")), expected)


if __name__ == "__main__":
    unittest.main()
