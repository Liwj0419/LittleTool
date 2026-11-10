"""自检：不开窗口，把「找窗口 -> 截图 -> 识别」整条链路跑一遍，结果写 selftest.log。"""

from __future__ import annotations

import os
import time

from . import config as cfgmod
from . import screen
from .ocr import Ocr


def run() -> int:
    lines: list[str] = []

    def say(text: str) -> None:
        lines.append(text)
        print(text)

    say("== read_text 自检 ==")
    say("平台: %s" % ("Windows" if os.name == "nt" else os.name))
    say("DPI 缩放: %.2f" % screen.dpi_scale())
    vx, vy, vw, vh = screen.virtual_screen()
    say("虚拟屏: %dx%d @ (%d,%d)" % (vw, vh, vx, vy))

    cfg = cfgmod.Config.load()
    say("配置: %s" % cfg.path)
    say("唤起热键: %s" % (cfg.hotkey() or "(未配置)"))

    o = Ocr(cfg.get("ocr.upscale", 2))
    t0 = time.time()
    ok = o.start()
    say("OCR 桥启动: %s（%.0f ms）" % ("成功" if ok else "失败", (time.time() - t0) * 1000))
    if not ok:
        _write(lines)
        return 1

    # 取鼠标下的窗口；拿不到就用屏幕中间一块兜底，保证链路能被验证
    rect = screen.window_rect_at_cursor()
    if rect is None:
        cx = vx + vw // 2 - 300
        cy = vy + vh // 2 - 120
        rect = (cx, cy, 600, 240)
        say("鼠标下没有窗口，改用屏幕中部 600x240")
    else:
        say("鼠标下的窗口: %dx%d @ (%d,%d)" % (rect[2], rect[3], rect[0], rect[1]))

    t0 = time.time()
    image = screen.grab(rect)
    say("截图: %dx%d（%.0f ms）" % (image.size[0], image.size[1], (time.time() - t0) * 1000))

    t0 = time.time()
    try:
        result = o.recognize(image)
    except Exception as e:  # noqa: BLE001
        say("识别失败: %r" % (e,))
        o.stop()
        _write(lines)
        return 1
    finally:
        image.close()

    words = sum(len(line) for line in result)
    say("识别: %d 行 / %d 个文字块（%.0f ms）" % (len(result), words, (time.time() - t0) * 1000))
    for i, line in enumerate(result[:8]):
        text = "".join(w["text"] for w in line)
        first = line[0]
        say(
            "  行%d  x=%d y=%d h=%d  %s"
            % (i, int(first["x"]), int(first["y"]), int(first["h"]), text[:40])
        )

    copied = screen.set_clipboard("read_text selftest")
    say("剪贴板写入: %s" % ("成功" if copied else "失败"))

    o.stop()
    _write(lines)
    say("== 自检结束，详见 selftest.log ==")
    return 0 if copied else 1


def _write(lines: list[str]) -> None:
    try:
        path = cfgmod.app_dir() / "selftest.log"
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    except OSError:
        pass
