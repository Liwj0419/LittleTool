"""本地 HTTP 服务：把 assets/index.html 和数据接口挂到 127.0.0.1 的随机端口上。

用 HTTP 而不是 ``file://`` 是因为页面需要一个正常来源：``navigator.clipboard``
和 ``fetch`` 在 127.0.0.1 上按普通网页的规矩来（Chromium 里算安全上下文）。
"""

from __future__ import annotations

import json
import os
import sys
import threading
import time
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlparse

from . import config as cfgmod

# 自检用：页面每次心跳把渲染状态回报到这里（正常运行时没人调这个接口）
LAST_PROBE: dict = {}

MIME = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".ico": "image/x-icon",
    ".png": "image/png",
    ".svg": "image/svg+xml",
}


def open_externally(path: str) -> bool:
    """用系统默认程序打开（Excel / WPS / 文本编辑器）。"""
    try:
        if sys.platform == "win32":
            os.startfile(path)                      # noqa: S606
        elif sys.platform == "darwin":
            import subprocess
            subprocess.Popen(["open", path])
        else:
            import subprocess
            subprocess.Popen(["xdg-open", path])
        return True
    except Exception as e:
        cfgmod.log(f"[open] {e!r}")
        return False


class Server:
    """起一个线程池 HTTP 服务，端口让系统分配。"""

    def __init__(self, viewer, hooks: dict):
        self.viewer = viewer
        self.hooks = hooks
        self.httpd: ThreadingHTTPServer | None = None
        self.port = 0

    def start(self, selftest: bool = False) -> str:
        handler = make_handler(self.viewer, self.hooks)
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.httpd.daemon_threads = True
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, name="http",
                         daemon=True).start()
        url = f"http://127.0.0.1:{self.port}/"
        return url + "?selftest=1" if selftest else url

    def stop(self) -> None:
        httpd, self.httpd = self.httpd, None
        if httpd is not None:
            try:
                httpd.shutdown()
                httpd.server_close()
            except Exception:
                pass


def make_handler(viewer, hooks: dict):
    """hooks: pick / show / hide / quit 四个回调。"""
    assets = cfgmod.assets_dir()

    class Handler(BaseHTTPRequestHandler):
        server_version = "excel_shower/3.0"

        def log_message(self, fmt, *args):       # 别往控制台刷请求日志
            pass

        def _send(self, code, body=b"", ctype="application/json; charset=utf-8"):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def _json(self, obj, code=200):
            self._send(code, json.dumps(obj, ensure_ascii=False).encode("utf-8"))

        def _file(self, name: str) -> None:
            path = assets / name
            if not path.is_file():
                self._send(404, b"not found", "text/plain; charset=utf-8")
                return
            with open(path, "rb") as f:
                body = f.read()
            self._send(200, body, MIME.get(path.suffix.lower(),
                                           "application/octet-stream"))

        def _hook(self, name):
            cb = hooks.get(name)
            if cb:
                try:
                    cb()
                except Exception as e:
                    cfgmod.log(f"[hook:{name}] {e!r}")

        def do_GET(self):
            parsed = urlparse(self.path)
            qs = parse_qs(parsed.query)
            path = parsed.path

            def q(name, default=""):
                return unquote((qs.get(name) or [default])[0])

            try:
                if path in ("/", "/index.html"):
                    self._file("index.html")
                    return

                if path == "/favicon.ico":
                    self._file("icon.ico")
                    return

                if path == "/api/state":
                    self._json(viewer.meta())
                    return

                if path == "/api/rows":
                    count = int(q("count", "0") or 0) or viewer.cfg.page_rows
                    self._json(viewer.rows(int(q("start", "0") or 0), count))
                    return

                if path == "/api/open":
                    target = q("path")
                    if not target:
                        self._json({"ok": False, "error": "缺少 path"}, 400)
                        return
                    self._json({"ok": viewer.open(target), "state": viewer.meta()})
                    return

                if path == "/api/pick":
                    start = q("dir") or os.path.dirname(
                        (viewer.doc.path if viewer.doc else "") or "")
                    pick = hooks.get("pick")
                    self._json({"path": pick(start) if pick else ""})
                    return

                if path == "/api/sheet":
                    viewer.set_sheet(q("name"))
                    self._json(viewer.meta())
                    return

                if path == "/api/search":
                    viewer.set_query(q("q"))
                    self._json(viewer.meta())
                    return

                if path == "/api/scope":
                    viewer.set_scope(q("mode"))
                    self._json(viewer.meta())
                    return

                if path == "/api/sort":
                    raw = q("col")
                    viewer.set_sort(int(raw) if raw not in ("", "none") else None)
                    self._json(viewer.meta())
                    return

                if path == "/api/cols":
                    raw = [x for x in q("list").split(",") if x.strip()]
                    viewer.set_cols([int(x) for x in raw
                                     if x.strip().lstrip("-").isdigit()])
                    self._json(viewer.meta())
                    return

                if path == "/api/range":
                    text, nrows, ncols = viewer.range_tsv(
                        int(q("r0", "0") or 0), int(q("r1", "0") or 0),
                        int(q("c0", "0") or 0), int(q("c1", "0") or 0))
                    self._json({"text": text, "rows": nrows, "cols": ncols})
                    return

                if path == "/api/cell":
                    self._json({"text": viewer.cell(int(q("r", "0") or 0),
                                                    int(q("c", "0") or 0))})
                    return

                if path == "/api/log":
                    cfgmod.log(f"[ui] {q('msg')}")
                    self._json({"ok": True})
                    return

                if path == "/api/probe":
                    # 带 d 是页面上报；不带 d 是取最近一次快照（自检/排查用）
                    if "d" not in qs:
                        self._json(dict(LAST_PROBE))
                        return
                    try:
                        LAST_PROBE.clear()
                        LAST_PROBE.update(json.loads(q("d")))
                        LAST_PROBE["_ts"] = time.time()
                    except ValueError:
                        pass
                    self._json({"ok": True})
                    return

                if path == "/api/reload":
                    viewer.reload()
                    self._json(viewer.meta())
                    return

                if path == "/api/jump":
                    idx = viewer.jump(q("sheet"), int(q("row", "0") or 0))
                    self._json({"index": idx, "state": viewer.meta()})
                    return

                if path == "/api/recent/clear":
                    self._json(viewer.clear_recent())
                    return

                if path == "/api/excel":
                    d = viewer.doc
                    ok = bool(d and os.path.exists(d.path)
                              and open_externally(d.path))
                    self._json({"ok": ok}, 200 if ok else 400)
                    return

                if path == "/api/activate":
                    self._hook("show")
                    self._json({"ok": True, "app": cfgmod.APP_NAME})
                    return

                if path == "/api/hide":
                    self._hook("hide")
                    self._json({"ok": True})
                    return

                if path == "/api/quit":
                    self._json({"ok": True})
                    cb = hooks.get("quit")
                    if cb:                     # 先把响应发回去，再退
                        threading.Timer(0.15, cb).start()
                    return

                self._json({"error": "not found"}, 404)
            except Exception as exc:
                self._json({"error": str(exc),
                            "trace": traceback.format_exc()[-800:]}, 500)

    return Handler
