"""识别：把一块截图交给 Windows 自带的 OCR 引擎。

不装第三方 OCR 库（见 AGENTS.md 第 8 条）。Windows 的 Windows.Media.Ocr
只能从 WinRT 调，Python 这边没有零依赖的调法，所以起一个常驻的
PowerShell 子进程当桥：Python 给图片路径，它回每个字的位置。
进程只启动一次，之后每次识别约几十毫秒。
"""

from __future__ import annotations

import os
import queue
import re
import subprocess
import threading
import time
import uuid

from . import config as cfgmod

IS_WIN = os.name == "nt"
CREATE_NO_WINDOW = 0x08000000

# 单次识别最多等这么久（秒）。超时说明桥进程状态不明，直接重启它
TIMEOUT = 25.0
START_TIMEOUT = 25.0

# WinRT/COM 的 HRESULT 用 .NET 的中文说出话来基本没法读，这里换成能动手的判断
_HRESULT_HINTS = {
    "0x88982f07": "图片格式认不出来（截图文件可能不完整）",
    "0x88982f50": "图片解码失败（PNG 编码器拒绝解码）",
    "0x88982f05": "图片索引越界（截图文件可能被截断）",
    "0x80070005": "没有权限读这张临时图片（可能被杀软或别的进程占着）",
    "0x80070002": "临时图片找不到了",
    "0x80070057": "OCR 引擎不接受这段参数（图片尺寸或像素格式异常）",
    "0x80070490": "系统 OCR 找不到需要的组件",
    "0x80004005": "系统 OCR 内部报错",
    "0x8007000e": "内存不够，图太大了",
}

# 这些错误是确定性的（图坏了 / 缺组件），重启桥进程再试一遍也只是白等
_NO_RETRY_HINTS = ("临时图片", "图片解码", "图片格式", "0x88982f", "找不到需要的组件")

_PATH_PART = re.compile(r"[A-Za-z0-9_./\\:~-]+")
_PATH_MARK = re.compile(r"[_/\\]|\.(?=[A-Za-z0-9_])")
_PATH_GLUE = ("_", "/", "\\")


class OcrError(RuntimeError):
    pass


def _friendly(msg: str) -> str:
    """桥脚本是纯 ASCII（见 AGENTS.md 第 4 条），它自己写的英文提示在这里翻成中文。"""
    text = msg.strip()
    low = text.lower()
    if low.startswith("temp image not found"):
        path = text.split(":", 1)[-1].strip()
        return "临时图片不见了：%s" % path if path else "临时图片不见了"
    if low.startswith("temp image is empty"):
        return "临时图片是空文件，截图没存下来"
    found = re.search(r"0x[0-9a-f]{8}", low)
    if found:
        hint = _HRESULT_HINTS.get(found.group(0))
        if hint:
            return "%s（%s）" % (hint, found.group(0))
    return text


def _path_runs(words):
    """按间距合并英文 OCR 拆开的路径片段，例如文件名和 .tga.meta。"""
    run = []
    for word in sorted(words, key=lambda word: word["x"]):
        if not _PATH_PART.fullmatch(word["text"]):
            if run:
                yield run
                run = []
            continue
        if run:
            prev = run[-1]
            gap = word["x"] - (prev["x"] + prev["w"])
            char_width = max(prev["w"] / len(prev["text"]), word["w"] / len(word["text"]))
            touches_separator = prev["text"].endswith(_PATH_GLUE) or word["text"].startswith((".", *_PATH_GLUE))
            if gap > char_width * (0.75 if touches_separator else 0.3):
                yield run
                run = []
        run.append(word)
    if run:
        yield run


def _overlaps(word, left, top, right, bottom):
    vertical = min(bottom, word["y"] + word["h"]) - max(top, word["y"])
    return (vertical > 0 and vertical >= 0.5 * min(word["h"], bottom - top)
            and word["x"] < right and word["x"] + word["w"] > left)


def _merge_english_paths(primary: list[list[dict]], english: list[list[dict]]) -> None:
    """英文结果补回路径符号或字符时，只替换同位置的 ASCII 文字。"""
    rows = [
        (min(word["y"] for word in line), max(word["y"] + word["h"] for word in line), line)
        for line in primary if line
    ]
    for words in english:
        for run in _path_runs(words):
            text = "".join(word["text"] for word in run)
            marks = len(_PATH_MARK.findall(text))
            if not marks or not any(char.isalnum() for char in text):
                continue
            left = run[0]["x"]
            top = min(word["y"] for word in run)
            right = max(word["x"] + word["w"] for word in run)
            bottom = max(word["y"] + word["h"] for word in run)

            matches = []
            for row_top, row_bottom, line in rows:
                if row_bottom <= top or row_top >= bottom:
                    continue
                covered = [i for i, word in enumerate(line) if _overlaps(word, left, top, right, bottom)]
                if covered:
                    matches.append((line, covered))
            if len(matches) != 1:
                continue
            line, covered = matches[0]
            start, end = covered[0], covered[-1] + 1
            old_words = line[start:end]
            if len(old_words) != len(covered) or any(not word["text"].isascii() for word in old_words):
                continue
            # 英文框只覆盖原字块一部分时，无法安全替换。
            if any(
                max(0, left - word["x"]) + max(0, word["x"] + word["w"] - right) > word["w"] * 0.2
                for word in old_words
            ):
                continue
            old_text = "".join(word["text"] for word in old_words)
            if (marks, len(text)) > (len(_PATH_MARK.findall(old_text)), len(old_text)):
                # 路径变成宽字块后，前后仍用原边缘字块的宽度判断空格。
                run[0]["join_left"] = old_words[0]["w"]
                run[-1]["join_right"] = old_words[-1]["w"]
                for word in run[1:]:
                    word["continues_path"] = True
                line[start:end] = run


class Ocr:
    def __init__(self, upscale: int = 2) -> None:
        self.upscale = max(1, min(3, int(upscale or 1)))
        self._proc: subprocess.Popen | None = None
        self._queue: queue.Queue = queue.Queue()
        self._lock = threading.Lock()

    # ---------------------------------------------------------------- 生命周期

    def start(self) -> bool:
        if not IS_WIN:
            cfgmod.log("[ocr] 非 Windows 平台，暂不支持识别")
            return False
        if self._proc is not None:
            return True

        script = cfgmod.assets_dir() / "ocr_bridge.ps1"
        if not script.exists():
            cfgmod.log("[ocr] 缺少桥脚本：%s" % script)
            return False

        flags = CREATE_NO_WINDOW if IS_WIN else 0
        try:
            self._proc = subprocess.Popen(
                [
                    "powershell.exe",
                    "-NoProfile",
                    "-NoLogo",
                    "-NonInteractive",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-File",
                    str(script),
                ],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                creationflags=flags,
            )
        except Exception as e:  # noqa: BLE001
            cfgmod.log("[ocr] 启动桥进程失败：%r" % (e,))
            self._proc = None
            return False

        # 每次启动用新的队列：上一轮遗留的行不能串到这一轮里
        box: queue.Queue = queue.Queue()
        self._queue = box
        proc = self._proc
        threading.Thread(target=self._pump, args=(proc, box), name="ocr-pump", daemon=True).start()
        threading.Thread(target=self._pump_err, args=(proc,), name="ocr-pump-err", daemon=True).start()

        deadline = time.time() + START_TIMEOUT
        while True:
            remain = deadline - time.time()
            if remain <= 0:
                cfgmod.log("[ocr] 桥进程启动超时")
                self.stop()
                return False
            try:
                first = box.get(timeout=remain)
            except queue.Empty:
                cfgmod.log("[ocr] 桥进程启动超时")
                self.stop()
                return False
            if first is None:
                cfgmod.log("[ocr] 桥进程没等到 ready 就退出了")
                self.stop()
                return False
            if first == "ready":
                return True
            # powershell 偶尔会先吐版权标语之类的杂行，记下来但不当失败
            cfgmod.log("[ocr] 启动时的额外输出：%r" % (first,))

    def _pump(self, proc: subprocess.Popen, box: queue.Queue) -> None:
        if proc.stdout is None:
            return
        try:
            for line in proc.stdout:
                box.put(line.rstrip("\r\n"))
        except (ValueError, OSError):
            pass  # 进程被收掉时管道会先失效，属于正常收场
        finally:
            box.put(None)

    def _pump_err(self, proc: subprocess.Popen) -> None:
        """桥进程的 stderr 以前直接丢掉，出问题时一点线索都没有。"""
        if proc.stderr is None:
            return
        kept = 0
        try:
            for line in proc.stderr:
                text = line.rstrip()
                if text and kept < 40:
                    kept += 1
                    cfgmod.log("[ocr] stderr: %s" % text)
        except (ValueError, OSError):
            pass

    def stop(self) -> None:
        proc, self._proc = self._proc, None
        if proc is None:
            return
        try:
            if proc.stdin:
                proc.stdin.write("QUIT\n")
                proc.stdin.flush()
        except Exception:  # noqa: BLE001
            pass
        try:
            proc.wait(timeout=2)
        except Exception:  # noqa: BLE001
            try:
                proc.kill()
                proc.wait(timeout=2)
            except Exception:  # noqa: BLE001
                pass
        # 管道不关掉的话，解释器退出时还会试着 flush 一次死掉的标准输入
        for stream in (proc.stdin, proc.stdout, proc.stderr):
            try:
                if stream is not None:
                    stream.close()
            except Exception:  # noqa: BLE001
                pass

    # ---------------------------------------------------------------- 识别

    def recognize(self, image) -> list[list[dict]]:
        """PIL.Image -> [[{text,x,y,w,h}, ...], ...]，坐标已折算回原图像素。"""
        if self._proc is None and not self.start():
            raise OcrError("识别引擎没启动起来")

        scale, big = self._resize(image)
        try:
            raw = self._recognize_file(big)
        finally:
            try:
                big.close()
            except Exception:  # noqa: BLE001
                pass
        if scale != 1.0:
            for line in raw:
                for word in line:
                    for field in ("x", "y", "w", "h", "join_left", "join_right"):
                        if field in word:
                            word[field] /= scale
        return raw

    def _recognize_file(self, image) -> list[list[dict]]:
        """存成 PNG 交给桥进程。

        目录顺序有讲究：`%TEMP%` 一定跨进程可见；`data_dir()`（%APPDATA%）在
        被重定向的环境里「自己存得进、别人看不见」，只能当退路。
        """
        folders = [cfgmod.temp_dir(), cfgmod.data_dir()]
        last: OcrError | None = None
        for i, folder in enumerate(folders):
            # 每次用独立文件名：上一轮的文件可能还被桥进程占着，覆盖写会失败
            path = folder / ("ocr_%s.png" % uuid.uuid4().hex[:10])
            try:
                image.save(str(path), "PNG")
            except Exception as e:  # noqa: BLE001
                last = OcrError("临时图片写入失败：%r" % (e,))
                continue
            try:
                return self._call_with_retry(str(path))
            except OcrError as e:
                last = e
                # 只有「文件对桥进程不可见」值得换个目录再来一次
                if "临时图片" not in str(e) or i == len(folders) - 1:
                    raise
                cfgmod.log("[ocr] %s，改用 %s 再试" % (e, folders[i + 1]))
            finally:
                try:
                    path.unlink()
                except OSError:
                    pass
        raise last or OcrError("识别失败")

    def _resize(self, image):
        from PIL import Image

        w, h = image.size
        longest = max(w, h)
        scale = float(self.upscale)

        # 大图不用放大：本来字就够大，放大只会让 OCR 慢好几倍
        if longest >= 1600:
            scale = 1.0
        # 超过 4000 再往回收，既省时间也避开引擎的舒适区
        if longest * scale > 4000:
            scale = 4000.0 / longest

        # 系统 OCR 对超大图有上限，兜一层
        limit = 9000.0
        if w * scale > limit or h * scale > limit:
            scale = min(limit / max(w, 1), limit / max(h, 1))
        scale = max(min(scale, 3.0), 0.2)

        if abs(scale - 1.0) < 0.01:
            return 1.0, image.copy()

        size = (max(1, int(round(w * scale))), max(1, int(round(h * scale))))
        big = image.resize(size, Image.LANCZOS)
        # 缩放系数用实际尺寸反推，避免取整误差累积到几十像素
        return (size[0] / float(w) if w else 1.0), big

    def _call_with_retry(self, path: str) -> list[list[dict]]:
        """一次失败先重启桥进程再试一次：WinRT 偶发抽风时不用用户重按热键。"""
        try:
            return self._call(path)
        except OcrError as e:
            # 确定性失败重试也没用：图片本身的问题、WIC 解不开、缺语言包
            if any(hint in str(e) for hint in _NO_RETRY_HINTS):
                raise
            cfgmod.log("[ocr] 识别失败（%s），重启桥进程后重试一次" % (e,))
            self.stop()
            if not self.start():
                raise OcrError("识别引擎没启动起来（%s）" % (e,)) from e
        return self._call(path)

    def _call(self, path: str) -> list[list[dict]]:
        with self._lock:
            proc = self._proc
            if proc is None or proc.stdin is None:
                raise OcrError("识别进程已退出")

            # 带请求号：上一轮迟到的结果能被认出来丢掉，不会串行错位
            rid = uuid.uuid4().hex[:8]
            line = "%s\t%s" % (rid, path.replace("\t", " ").replace("\n", " "))
            try:
                proc.stdin.write(line + "\n")
                proc.stdin.flush()
            except Exception as e:  # noqa: BLE001
                self.stop()
                raise OcrError("写入识别进程失败：%r" % (e,)) from e

            deadline = time.time() + TIMEOUT
            while True:
                head = self._next(deadline)
                kind, _, rest = head.partition("\t")
                if kind == "ok":
                    tag = rest.strip()
                    if tag and tag != rid:
                        self._skip_to_end(deadline)  # 吃掉上一轮的残留
                        continue
                    break
                if kind == "err":
                    tag, _, msg = rest.partition("\t")
                    if tag and tag != rid:
                        continue
                    text = _friendly(msg) or "识别失败"
                    cfgmod.log("[ocr] 桥进程报错：%s" % text)
                    raise OcrError(text)
                raise OcrError("识别返回了看不懂的内容：%r" % (head,))

            primary: dict[int, list[dict]] = {}
            english: dict[int, list[dict]] = {}
            lines = primary
            while True:
                try:
                    row = self._next(deadline)
                except OcrError as e:
                    if lines is primary:
                        raise
                    cfgmod.log("[ocr] 英文复识别失败，保留原结果：%s" % e)
                    english.clear()
                    break
                if row == "end":
                    break
                if row == "english":
                    lines = english
                    continue
                parts = row.split("\t")
                if len(parts) < 6:
                    continue
                try:
                    idx = int(parts[0])
                    word = {
                        "x": float(parts[1]),
                        "y": float(parts[2]),
                        "w": float(parts[3]),
                        "h": float(parts[4]),
                        "text": "\t".join(parts[5:]),
                    }
                except ValueError:
                    continue
                lines.setdefault(idx, []).append(word)

        ordered = [primary[k] for k in sorted(primary)]
        if english:
            _merge_english_paths(ordered, list(english.values()))
        return ordered

    def _next(self, deadline: float) -> str:
        remain = deadline - time.time()
        if remain <= 0:
            self._timed_out()
        try:
            row = self._queue.get(timeout=remain)
        except queue.Empty:
            self._timed_out()
        if row is None:
            self._proc = None
            raise OcrError("识别进程退出了")
        return row

    def _skip_to_end(self, deadline: float) -> None:
        while True:
            row = self._next(deadline)
            if row == "end" or row.startswith("err"):
                return

    def _timed_out(self) -> None:
        # 超时后桥进程状态不可知，杀掉，下一次调用会重新拉起
        self.stop()
        raise OcrError("识别超时（%.0f 秒没响应）" % TIMEOUT)
