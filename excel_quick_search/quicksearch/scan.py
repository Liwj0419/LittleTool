"""扫描数据源里的 Excel 文件。

不解析表格内容，只看文件名 —— 和旧版行为一致：
递归 os.walk，跳过 `_win_` 开头的临时文件，扩展名命中就收进来。
"""

from __future__ import annotations

import os
import time
from datetime import datetime


def format_size(size: int) -> str:
    value = float(size)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024.0:
            return f"{value:.1f} {unit}"
        value /= 1024.0
    return f"{value:.1f} TB"


def format_time(timestamp: float) -> str:
    return datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d %H:%M")


def _entry(full_path: str, rel_dir: str) -> dict | None:
    try:
        st = os.stat(full_path)
    except OSError:
        return None
    return {
        "name": os.path.basename(full_path),
        "path": full_path,
        "dir": rel_dir,
        "size": st.st_size,
        "sizeText": format_size(st.st_size),
        "mtime": st.st_mtime,
        "timeText": format_time(st.st_mtime),
    }


def scan(source: dict, options: dict) -> dict:
    """扫一个数据源。返回给页面的字典，失败时 ok = False。"""
    path = str(source.get("path") or "")
    name = str(source.get("name") or path)
    exts = tuple(
        e.lower() for e in (options.get("extensions") or [".xlsx"]) if isinstance(e, str)
    )
    skips = tuple(
        s.lower() for s in (options.get("skip_prefix") or []) if isinstance(s, str)
    )
    try:
        limit = int(options.get("max_results") or 0)
    except (TypeError, ValueError):
        limit = 0
    try:
        max_depth = int(options.get("max_depth") or 0)
    except (TypeError, ValueError):
        max_depth = 0

    result = {
        "ok": False,
        "key": str(source.get("key", "")),
        "name": name,
        "path": path,
        "files": [],
        "count": 0,
        "truncated": False,
        "elapsed": 0.0,
        "error": "",
    }

    if not path:
        result["error"] = "这个数据源没有配置路径"
        return result
    if not os.path.exists(path):
        result["error"] = f"路径不存在：{path}"
        return result

    started = time.perf_counter()
    files: list[dict] = []

    try:
        if os.path.isfile(path):
            if path.lower().endswith(exts) and not _skipped(os.path.basename(path), skips):
                item = _entry(path, "")
                if item:
                    files.append(item)
        else:
            base = os.path.abspath(path)
            for root_dir, dirs, names in os.walk(base):
                rel = os.path.relpath(root_dir, base)
                depth = 0 if rel == "." else rel.count(os.sep) + 1
                if max_depth and depth >= max_depth:
                    dirs[:] = []
                for file_name in names:
                    if _skipped(file_name, skips):
                        continue
                    if not file_name.lower().endswith(exts):
                        continue
                    full = os.path.join(root_dir, file_name)
                    item = _entry(full, "" if rel == "." else rel)
                    if item:
                        files.append(item)
                if limit and len(files) >= limit:
                    result["truncated"] = True
                    break
    except Exception as e:  # 权限、坏盘符之类，别让整个界面挂掉
        result["error"] = f"扫描失败：{e!r}"
        return result

    files.sort(key=lambda f: (f["name"].lower(), f["path"].lower()))
    if limit and len(files) > limit:
        files = files[:limit]
        result["truncated"] = True

    result.update(
        ok=True,
        files=files,
        count=len(files),
        elapsed=round(time.perf_counter() - started, 3),
    )
    return result


def _skipped(file_name: str, skips: tuple[str, ...]) -> bool:
    if not skips:
        return False
    lower = file_name.lower()
    return any(lower.startswith(s) for s in skips if s)
