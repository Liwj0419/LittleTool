"""只读地解析表格文件（纯标准库，不需要 openpyxl）。

* ``.xlsx`` / ``.xlsm``：用 ``zipfile`` 直接读 OOXML 包，``ElementTree`` 流式解析
  sheet XML，共享字符串、日期格式、列宽都能还原。
* ``.csv`` / ``.tsv`` / ``.txt``：自动识别编码和分隔符。

对外只有 ``load_table()`` 一个入口，返回 ``Table``。
"""

from __future__ import annotations

import csv
import io
import os
import re
import time
import zipfile
from datetime import datetime, timedelta
from xml.etree import ElementTree as ET

from .config import DEFAULT_MAX_ROWS

NS_MAIN = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
NS_REL = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"

# Excel 内置的日期/时间格式编号
BUILTIN_DATE_FMT = set(range(14, 23)) | {27, 30, 36, 45, 46, 47, 50, 57}

_DATE_HINT = re.compile(r"[ymdhs]", re.IGNORECASE)

SUPPORTED = (
    ("表格文件", "*.xlsx *.xlsm *.csv *.tsv *.txt"),
    ("Excel 工作簿", "*.xlsx *.xlsm"),
    ("CSV / TSV", "*.csv *.tsv *.txt"),
    ("所有文件", "*.*"),
)


# --------------------------------------------------------------------------- 小工具


def col_letter(i: int) -> str:
    """0 -> A, 25 -> Z, 26 -> AA"""
    s = ""
    i += 1
    while i:
        i, rem = divmod(i - 1, 26)
        s = chr(65 + rem) + s
    return s


def disp_len(s: str) -> int:
    """按东亚字符宽度估算显示长度，用于算列宽。"""
    n = 0
    for ch in s:
        n += 2 if ord(ch) > 0x2E80 else 1
    return n


def trim_empty(rows):
    """去掉尾部的整行、整列空白。

    Excel 文件里经常存在"只带格式、没有内容"的空行空列，
    直接显示会多出一堆没用的列，看着很乱。
    """
    while rows and not any(x.strip() for x in rows[-1]):
        rows.pop()
    if not rows:
        return rows

    width = max(len(r) for r in rows)
    last = -1
    for r in rows:
        for i in range(len(r) - 1, last, -1):
            if r[i].strip():
                last = i
                break
        if last >= width - 1:
            break
    if last + 1 < width:
        for r in rows:
            del r[last + 1:]
    return rows


def friendly_error(path: str, exc: BaseException | None) -> str:
    """把底层异常翻译成一句人话。"""
    name = os.path.basename(path)
    if name.startswith("~$"):
        return ("这是 Excel 打开文件时生成的临时锁定文件，不是真正的表格。\n"
                "请选择同目录下不带 ~$ 前缀的那个文件。")
    if isinstance(exc, zipfile.BadZipFile):
        return ("这个文件读不出来，可能已损坏、正在被写入，"
                "或者根本不是 xlsx 格式。")
    if isinstance(exc, PermissionError):
        return "没有读取这个文件的权限。"
    if isinstance(exc, FileNotFoundError):
        return "文件不见了，可能已被移动或删除。"
    if isinstance(exc, KeyError):
        return "文件结构不完整（缺少工作簿主体），无法解析。"
    return f"读取失败：\n{exc}"


def _col_index(ref: str) -> int:
    """单元格引用 -> 0 基列号。 'A1'->0, 'AB12'->27"""
    n = 0
    for ch in ref:
        if "A" <= ch <= "Z":
            n = n * 26 + (ord(ch) - 64)
        elif "a" <= ch <= "z":
            n = n * 26 + (ord(ch) - 96)
        else:
            break
    return n - 1


def _num_to_text(v: float) -> str:
    """数字转成给人看的字符串，避免 1.0 这种尾巴。"""
    if v != v or v in (float("inf"), float("-inf")):
        return str(v)
    if v.is_integer():
        if abs(v) < 1e16:
            return str(int(v))
    return "%.10g" % v


def _serial_to_text(v, date1904: bool = False) -> str:
    """Excel 日期序列号 -> 文本。"""
    try:
        base = datetime(1904, 1, 1) if date1904 else datetime(1899, 12, 30)
        dt = base + timedelta(days=float(v))
    except (OverflowError, ValueError, OSError):
        return _num_to_text(v)
    if (dt.hour, dt.minute, dt.second) == (0, 0, 0):
        return dt.strftime("%Y-%m-%d")
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def _is_date_format(code: str | None) -> bool:
    """判断自定义格式串是不是日期/时间格式。"""
    if not code:
        return False
    s = re.sub(r'\[[^\]]*\]', "", code)     # 去掉 [Red] / [$-409] 这类
    s = re.sub(r'"[^"]*"', "", s)           # 去掉引号里的字面量
    s = s.split(";")[0]                     # 只看正数段
    if "general" in s.lower():
        return False
    # 至少要有 y/m/d/h/s，且不能是 0.00E+00 这类科学计数
    if "e+" in s.lower() or "e-" in s.lower():
        return False
    return len(_DATE_HINT.findall(s)) >= 1 and not re.fullmatch(r"[\d\s.,%#?]+", s)


# --------------------------------------------------------------------------- XLSX


class Workbook:
    """只读地打开一个 xlsx/xlsm，按需解析单个 sheet。"""

    def __init__(self, path: str):
        self.path = path
        self._zf = zipfile.ZipFile(path, "r")
        self._sst = None
        self._date_styles = None
        self.date1904 = False
        self.sheets = []          # [(name, zip_entry)]
        self._parse_workbook()

    def close(self) -> None:
        try:
            self._zf.close()
        except Exception:
            pass

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        self.close()

    # -- 内部 ------------------------------------------------------------- #
    def _parse_workbook(self) -> None:
        wb = ET.fromstring(self._zf.read("xl/workbook.xml"))
        pr = wb.find(NS_MAIN + "workbookPr")
        if pr is not None and pr.get("date1904") in ("1", "true"):
            self.date1904 = True

        rels = {}
        try:
            rx = ET.fromstring(self._zf.read("xl/_rels/workbook.xml.rels"))
            for rel in rx:
                rels[rel.get("Id")] = rel.get("Target")
        except KeyError:
            pass

        for sh in wb.iter(NS_MAIN + "sheet"):
            name = sh.get("name") or "Sheet"
            target = rels.get(sh.get(NS_REL + "id"))
            if not target:
                continue
            target = target.replace("\\", "/")
            if target.startswith("/"):
                entry = target.lstrip("/")
            elif target.startswith("xl/"):
                entry = target
            else:
                entry = "xl/" + target
            if entry in self._zf.namelist():
                self.sheets.append((name, entry))

    def _shared_strings(self) -> list:
        if self._sst is not None:
            return self._sst
        out: list[str] = []
        try:
            data = self._zf.read("xl/sharedStrings.xml")
        except KeyError:
            self._sst = out
            return out
        for _, el in ET.iterparse(io.BytesIO(data), events=("end",)):
            if el.tag == NS_MAIN + "si":
                out.append("".join(t.text or "" for t in el.iter(NS_MAIN + "t")))
                el.clear()
        self._sst = out
        return out

    def _date_style_flags(self) -> set:
        """返回 set，里面是"日期格式"的单元格样式下标。"""
        if self._date_styles is not None:
            return self._date_styles
        flags: set[int] = set()
        try:
            root = ET.fromstring(self._zf.read("xl/styles.xml"))
        except KeyError:
            self._date_styles = flags
            return flags

        custom = {}
        for nf in root.iter(NS_MAIN + "numFmt"):
            try:
                custom[int(nf.get("numFmtId"))] = nf.get("formatCode") or ""
            except (TypeError, ValueError):
                pass

        xfs = root.find(NS_MAIN + "cellXfs")
        if xfs is not None:
            for idx, xf in enumerate(xfs):
                try:
                    fid = int(xf.get("numFmtId") or 0)
                except ValueError:
                    continue
                if fid in BUILTIN_DATE_FMT or _is_date_format(custom.get(fid)):
                    flags.add(idx)
        self._date_styles = flags
        return flags

    # -- 对外 ------------------------------------------------------------- #
    def sheet_names(self) -> list:
        return [n for n, _ in self.sheets]

    def read_sheet(self, name=None, max_rows: int = DEFAULT_MAX_ROWS):
        """读取一个 sheet，返回 (rows, col_widths, truncated)。"""
        entry = None
        for n, e in self.sheets:
            if name is None or n == name:
                entry = e
                break
        if entry is None:
            if not self.sheets:
                return [], {}, False
            entry = self.sheets[0][1]

        sst = self._shared_strings()
        date_styles = self._date_style_flags()
        rows: list[list[str]] = []
        col_widths: dict[int, float] = {}
        truncated = False
        max_width = 0

        with self._zf.open(entry) as fh:
            for _event, el in ET.iterparse(fh, events=("end",)):
                tag = el.tag

                if tag == NS_MAIN + "col":
                    # <col min max width/> 取 Excel 里的列宽，能还原作者的排版
                    try:
                        w = float(el.get("width") or 0)
                        lo = int(el.get("min"))
                        hi = min(int(el.get("max") or lo), lo + 200)
                        for i in range(lo, hi + 1):
                            if w > 0:
                                col_widths[i - 1] = w
                    except (TypeError, ValueError):
                        pass
                    el.clear()

                elif tag == NS_MAIN + "row":
                    if len(rows) >= max_rows:
                        truncated = True
                        el.clear()
                        break
                    cells = {}
                    for c in el.iter(NS_MAIN + "c"):
                        ref = c.get("r")
                        if not ref:
                            continue
                        ci = _col_index(ref)
                        if ci < 0:
                            continue
                        cells[ci] = self._cell_text(c, sst, date_styles)
                    if cells:
                        width = max(cells) + 1
                        if width > max_width:
                            max_width = width
                        rows.append([cells.get(i, "") for i in range(width)])
                    else:
                        rows.append([])
                    el.clear()

        # 补齐成矩形
        for r in rows:
            if len(r) < max_width:
                r.extend([""] * (max_width - len(r)))

        return trim_empty(rows), col_widths, truncated

    @staticmethod
    def _cell_text(c, sst, date_styles) -> str:
        t = c.get("t")

        if t == "inlineStr":
            return "".join(x.text or "" for x in c.iter(NS_MAIN + "t"))

        v = c.find(NS_MAIN + "v")
        if v is None or v.text is None:
            return ""
        raw = v.text

        if t == "s":
            try:
                return sst[int(raw)]
            except (ValueError, IndexError):
                return raw
        if t in ("str", "e"):
            return raw
        if t == "b":
            return "TRUE" if raw == "1" else "FALSE"

        try:
            f = float(raw)
        except ValueError:
            return raw

        try:
            style = int(c.get("s") or 0)
        except ValueError:
            style = 0
        if style in date_styles:
            return _serial_to_text(f)
        return _num_to_text(f)


# --------------------------------------------------------------------------- CSV


def read_delimited(path: str, max_rows: int = DEFAULT_MAX_ROWS):
    raw = open(path, "rb").read(4 * 1024 * 1024)
    text = None
    for enc in ("utf-8-sig", "gbk", "utf-16", "big5", "latin-1"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    if text is None:
        text = raw.decode("utf-8", "replace")

    sample = text[:8192]
    lower = path.lower()
    if lower.endswith(".tsv"):
        delimiter = "\t"
    elif lower.endswith(".csv"):
        delimiter = ","
    else:
        try:
            delimiter = csv.Sniffer().sniff(sample, delimiters=",\t;|").delimiter
        except csv.Error:
            delimiter = ","

    rows: list[list[str]] = []
    truncated = False
    for row in csv.reader(io.StringIO(text), delimiter=delimiter):
        if len(rows) >= max_rows:
            truncated = True
            break
        rows.append([str(x) for x in row])
    width = max((len(r) for r in rows), default=0)
    for r in rows:
        if len(r) < width:
            r.extend([""] * (width - len(r)))
    return trim_empty(rows), {}, truncated


# --------------------------------------------------------------------------- 表格


class Table:
    def __init__(self, path, headers, rows, sheet="", col_widths=None,
                 truncated=False, elapsed=0.0):
        self.path = path
        self.sheet = sheet
        self.headers = headers
        self.rows = rows
        self.col_widths = col_widths or {}
        self.truncated = truncated
        self.elapsed = elapsed

    @property
    def ncols(self) -> int:
        return len(self.headers)

    @property
    def nrows(self) -> int:
        return len(self.rows)


def load_table(path: str, sheet=None, has_header: bool = True,
               max_rows: int = DEFAULT_MAX_ROWS) -> Table:
    """统一入口：按扩展名挑解析器，返回 Table。"""
    t0 = time.perf_counter()
    ext = os.path.splitext(path)[1].lower()

    if ext in (".csv", ".tsv", ".txt"):
        rows, widths, trunc = read_delimited(path, max_rows)
        sheet_name = ""
    else:
        with Workbook(path) as wb:
            names = wb.sheet_names()
            if sheet is None:
                sheet = names[0] if names else None
            rows, widths, trunc = wb.read_sheet(sheet, max_rows)
            sheet_name = sheet or ""

    ncols = max((len(r) for r in rows), default=0)
    if has_header and rows:
        headers = [str(x).strip() or col_letter(i) for i, x in enumerate(rows[0])]
        data = rows[1:]
    else:
        headers = [col_letter(i) for i in range(ncols)]
        data = rows

    if len(headers) < ncols:
        headers += [col_letter(i) for i in range(len(headers), ncols)]
    for r in data:
        if len(r) < ncols:
            r.extend([""] * (ncols - len(r)))

    return Table(path, headers, data, sheet_name, widths, trunc,
                 time.perf_counter() - t0)
