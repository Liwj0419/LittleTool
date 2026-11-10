"""pystray 托盘：右下角只留 Manager 这一个图标。"""

from __future__ import annotations

import threading

import pystray
from PIL import Image

from . import config as cfgmod
from . import native as n


class Tray:
    def __init__(self, app) -> None:
        self.app = app
        self.icon: pystray.Icon | None = None
        self._thread: threading.Thread | None = None

    # ---------------------------------------------------------------- 生命周期

    def start(self) -> None:
        try:
            self.icon = pystray.Icon(
                "plugmanager", self._image(), self.title(), self._menu()
            )
            self._thread = threading.Thread(
                target=self.icon.run, name="tray", daemon=True
            )
            self._thread.start()
        except Exception as e:
            cfgmod.log(f"[tray] 托盘启动失败：{e!r}")

    def stop(self) -> None:
        icon, self.icon = self.icon, None
        if icon is not None:
            try:
                icon.stop()
            except Exception:
                pass

    def refresh(self) -> None:
        if self.icon is None:
            return
        try:
            self.icon.title = self.title()
            self.icon.update_menu()
        except Exception:
            pass

    # ---------------------------------------------------------------- 内容

    def title(self) -> str:
        running = sum(
            1 for s in self.app.statuses().values() if s.get("running")
        )
        return f"Manager · {running}/{len(self.app.apps)} 在跑"

    @staticmethod
    def _image() -> Image.Image:
        path = cfgmod.assets_dir() / "icon.ico"
        try:
            return Image.open(path).convert("RGBA")
        except Exception as e:
            cfgmod.log(f"[tray] 图标读取失败：{e!r}")
            return Image.new("RGBA", (64, 64), (91, 156, 255, 255))

    def _menu(self) -> pystray.Menu:
        app = self.app
        return pystray.Menu(
            pystray.MenuItem("打开 Manager", lambda: app.show_window(), default=True),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("全部启动", lambda: app.start_all()),
            pystray.MenuItem("全部停止", lambda: app.stop_all()),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("打开软件目录", lambda: n.open_url(str(cfgmod.PLUG_ROOT))),
            pystray.MenuItem("编辑配置", lambda: app.open_manager_config()),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("退出", lambda: app.quit()),
        )
