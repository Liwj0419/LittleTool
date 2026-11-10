"""文档模型 + 视图状态（搜索、排序、列显示、分页取行）。

"视图"就是一串 ``(工作表, Excel 行号, 行数据)``。搜索和排序都在这里做完，
页面只管按 offset 取一段去画，所以多大数据都不会把界面拖死。

列布局是**按列名对齐**的：全表搜索要把列结构不同的几张表混在一屏里，
所以表头取各表列名的并集（当前表在前），每行按列名映射到自己的列。
同一个表头下面永远是同一个字段，不会出现"值串到别的列去"。
"""

from __future__ import annotations

import os
import threading

from .config import Config, State
from .sheet import Table, Workbook, col_letter, disp_len, friendly_error, load_table

ROW_H = 26                  # 行高（px），前端和后端算列宽都按这个来

# 列宽规则：max(内容最长, 标题) 字符数 + 缓冲，超过上限就截断
COL_CHARS_MAX = 40          # 最多按 40 个字符算宽
COL_MIN_PX = 60
COL_MAX_PX = 360
COL_PAD_PX = 26
COL_CHAR_PX = 7.2           # 一个西文字符大约多宽（Microsoft YaHei UI 9pt）


def _sort_key(row, col):
    v = row[col] if col < len(row) else ""
    if v == "":
        return (2, 0.0, "")
    try:
        return (0, float(v), "")
    except ValueError:
        return (1, 0.0, v.lower())


class Doc:
    """一个打开的文件：工作表清单 + 各表解析结果（按需读）。"""

    def __init__(self, path: str, has_header: bool = True,
                 max_rows: int = 200_000):
        self.path = path
        self.has_header = has_header
        self.max_rows = max_rows
        self.sheets: list[str] = []      # 所有工作表名
        self.tables: dict[str, Table] = {}   # 工作表名 -> Table
        self.table: Table | None = None      # 当前工作表

    @property
    def name(self) -> str:
        return os.path.basename(self.path or "")

    def load(self, sheet=None) -> Table:
        ext = os.path.splitext(self.path)[1].lower()
        self.sheets = []
        if ext in (".xlsx", ".xlsm"):
            with Workbook(self.path) as wb:
                self.sheets = wb.sheet_names()
        self.table = load_table(self.path, sheet, self.has_header, self.max_rows)
        self.tables[self.table.sheet or ""] = self.table
        return self.table

    def table_for(self, name: str) -> Table:
        if name in self.tables:
            return self.tables[name]
        t = load_table(self.path, name, self.has_header, self.max_rows)
        self.tables[name] = t
        return t

    def all_tables(self) -> dict:
        """全表搜索用：把每个工作表都读进来（只读一次）。"""
        for name in self.sheets:
            if name not in self.tables:
                try:
                    self.table_for(name)
                except Exception:      # 某个表坏了不影响别的
                    continue
        return self.tables


class Viewer:
    """整个应用的数据状态。所有改状态的方法都拿 self.lock，多线程安全。"""

    def __init__(self, cfg: Config, state: State, on_change=None):
        self.cfg = cfg
        self.state = state
        self.on_change = on_change          # 文件名变了 -> 改标题栏
        self.recent = [p for p in state.recent][:cfg.max_recent]
        self.lock = threading.RLock()
        self.doc: Doc | None = None
        self.query = ""
        self.scope = "all"        # all = 搜全部工作表；sheet = 只搜当前表
        self.sort_col = None
        self.sort_desc = False
        self.visible_cols: list[int] = []
        self.view: list[tuple] = []          # [(sheet, rowno, 对齐后的行数据)]
        self.error = ""
        self.loading = False
        self.rev = 0              # 每次状态变化 +1，页面据此判断要不要重画
        self._blobs = None
        # 列布局（表头 + 每张表 → 列下标的映射）与列宽，_rebuild 时一并算好
        self._heads: list[str] = []          # 统一表头的列名
        self._maps: dict[str, list] = {}     # 表名 -> [源列下标 or None]
        self._widths: list[int] = []         # 与 _heads 一一对应的列宽
        self._layout_n = 0                   # 上一次布局的列数（新增列自动补显）
        self._ident: set[str] = set()        # 列序与统一表头一致的工作表名

    # -- 打开 -------------------------------------------------------------- #
    def open(self, path: str, sheet=None) -> bool:
        path = os.path.abspath(path)
        name = os.path.basename(path)
        ext = os.path.splitext(path)[1].lower()

        msg = ""
        if not os.path.exists(path):
            msg = f"文件不存在：\n{path}"
        elif name.startswith("~$"):
            msg = friendly_error(path, None)
        elif ext == ".xls":
            msg = ("不支持旧版 .xls 格式。\n"
                   "请用 Excel / WPS 另存为 .xlsx 后再打开。")
        if msg:
            with self.lock:
                self.doc = None
                self.view = []
                self.error = msg
                self.loading = False
                self.rev += 1
            self._changed()
            return False

        with self.lock:
            self.loading = True
            self.error = ""
            self.rev += 1

        try:
            doc = Doc(path, max_rows=self.cfg.max_rows)
            doc.load(sheet)
        except Exception as exc:
            with self.lock:
                self.loading = False
                self.error = friendly_error(path, exc)
                self.rev += 1
            self._changed()
            return False

        with self.lock:
            self.doc = doc
            self.error = ""
            self.loading = False
            self.query = ""
            self.sort_col = None
            self.sort_desc = False
            self._blobs = None
            self._layout_n = 0
            self.visible_cols = list(range(doc.table.ncols))
            self._remember(path)
            self._rebuild()
            self.rev += 1
        self._changed()
        return True

    def open_async(self, path: str, sheet=None) -> None:
        """别堵住调用方（比如 IPC 线程 / 启动时）。"""
        threading.Thread(target=self.open, args=(path, sheet),
                         daemon=True).start()

    def set_sheet(self, name: str) -> None:
        with self.lock:
            d = self.doc
            if not d or not name or name == (d.table.sheet or ""):
                return
            try:
                d.table = d.table_for(name)
            except Exception as exc:
                self.error = friendly_error(d.path, exc)
                self.rev += 1
                return
            self.sort_col = None
            self.sort_desc = False
            self._blobs = None
            self._layout_n = 0
            self.visible_cols = list(range(d.table.ncols))
            self._rebuild()
            self.rev += 1

    def reload(self) -> None:
        with self.lock:
            d = self.doc
            if not d:
                return
            path, sheet = d.path, (d.table.sheet or "") if d.table else None
        self.open(path, sheet or None)

    def jump(self, sheet: str, rowno: int) -> int:
        """从全表搜索结果跳到某表的某行：清搜索、切表，返回该行在视图里的下标。"""
        with self.lock:
            d = self.doc
            if not d:
                return 0
            if sheet and sheet != (d.table.sheet or ""):
                try:
                    d.table = d.table_for(sheet)
                except Exception:
                    pass
            self.query = ""
            self.sort_col = None
            self.sort_desc = False
            self._layout_n = 0
            self.visible_cols = list(range(d.table.ncols))
            self._rebuild()
            self.rev += 1
            for i, item in enumerate(self.view):
                if item[1] == rowno:
                    return i
            return 0

    def clear_recent(self) -> dict:
        """清空历史，但保留当前正在看的这个文件。"""
        with self.lock:
            keep = self.doc.path if self.doc else None
            self.recent = [keep] if keep else []
            self.state.recent = list(self.recent)
            self.state.save()
            self.rev += 1
            return self.meta()

    # -- 搜索 / 排序 / 列 -------------------------------------------------- #
    def set_query(self, q: str) -> None:
        with self.lock:
            q = (q or "").strip()
            if q == self.query:
                return
            self.query = q
            self._rebuild()
            self.rev += 1

    def set_scope(self, mode: str) -> None:
        with self.lock:
            mode = "all" if mode == "all" else "sheet"
            if mode == self.scope:
                return
            self.scope = mode
            self._rebuild()
            self.rev += 1

    def set_sort(self, col) -> None:
        """点表头：升序 -> 降序 -> 取消。"""
        with self.lock:
            if col is None:
                return
            if self.sort_col != col:
                self.sort_col, self.sort_desc = col, False
            elif not self.sort_desc:
                self.sort_desc = True
            else:
                self.sort_col, self.sort_desc = None, False
            self._rebuild()
            self.rev += 1

    def set_cols(self, cols) -> None:
        with self.lock:
            n = len(self._heads) or (self.doc.table.ncols if self.doc and self.doc.table else 0)
            if not n:
                return
            valid = sorted({c for c in cols if 0 <= c < n})
            if not valid:
                return
            self.visible_cols = valid
            self.rev += 1

    # -- 内部 -------------------------------------------------------------- #
    def _changed(self) -> None:
        if self.on_change:
            try:
                self.on_change()
            except Exception:
                pass

    def _remember(self, path: str) -> None:
        self.recent = [path] + [p for p in self.recent if p != path]
        self.recent = self.recent[:self.cfg.max_recent]
        self.state.recent = list(self.recent)
        self.state.save()

    @property
    def show_sheet_col(self) -> bool:
        """全表搜索：结果里带工作表名（决定是否多读几张表、是否合并列布局）。"""
        d = self.doc
        return (self.scope == "all" and bool(self.query)
                and bool(d) and len(d.sheets) > 1)

    @property
    def show_jump_col(self) -> bool:
        """搜索结果的每行第一格都是「→ 前往」。

        以前这一列只在「全表搜索 + 多个工作表」时才有，于是只搜当前表、
        或者文件本来就只有一张表（CSV 更是连 sheets 都没有）时，
        搜索结果里一行「前往」都没有，想回到原表那一行只能自己滚。
        """
        return bool(self.query) and bool(self.doc) and bool(self.doc.table)

    # -- 列布局 ------------------------------------------------------------ #
    def _layout_tables(self) -> list:
        """参与当前布局的工作表 [(表名, Table)]。单表就是当前表本身。"""
        d = self.doc
        if not d or not d.table:
            return []
        if not self.show_sheet_col:
            return [(d.table.sheet or "", d.table)]
        # 全表搜索：当前表排最前，其余按工作簿顺序；列结构不同的表也要并进来
        d.all_tables()
        order: list[str] = []
        for name in [d.table.sheet or ""] + list(d.sheets):
            if name not in order:
                order.append(name)
        return [(n, d.tables[n]) for n in order if n in d.tables]

    def _layout(self):
        """统一列布局：返回 (表头列名, {表名: [源列下标 or None]})。

        关键点：**表头取各表列名的并集，行数据按列名对齐**。
        以前全表搜索是「表头用列最多的那张表，行数据按各自的列下标填」，
        两张表列结构不同就会串列——值跑到别的表头底下，看着就是"对不上"。
        """
        tables = self._layout_tables()
        self._ident = set()
        if not tables:
            return [], {}

        heads: list[str] = []
        seen = set()
        for _name, t in tables:
            for h in t.headers:
                if h not in seen:
                    seen.add(h)
                    heads.append(h)

        maps: dict[str, list] = {}
        ident: set[str] = set()          # 列序和统一表头完全一致的表：行数据不用复制
        for name, t in tables:
            own: dict[str, int] = {}
            for i, h in enumerate(t.headers):
                own.setdefault(h, i)       # 同名重复列只看第一个
            mp = [own.get(h) for h in heads]
            maps[name] = mp
            if mp == list(range(len(t.headers))):
                ident.add(name)
        self._ident = ident
        return heads, maps

    def _align(self, sheet: str, row) -> list:
        """把某张表的一行数据按列名搬到统一表头上（对不上的列留空）。"""
        mp = self._maps.get(sheet)
        if mp is None:
            mp = self._maps.get("") or []
        n = len(row)
        if sheet in self._ident and n == len(mp):
            return row                     # 单表布局：原样用，不复制
        return [row[j] if j is not None and j < n else "" for j in mp]

    def _sync_visible(self) -> None:
        """列数变了（合并搜索把别的表的列并进来）就给新列补上显示位。"""
        n = len(self._heads)
        prev = self._layout_n
        vis = [i for i in self.visible_cols if 0 <= i < n]
        if n > prev:
            vis += [i for i in range(max(prev, 0), n) if i not in vis]
        if not vis:
            vis = list(range(n))
        self.visible_cols = sorted(set(vis))
        self._layout_n = n

    @property
    def n_disp_cols(self) -> int:
        return len(self.visible_cols) + (2 if self.show_jump_col else 1)

    def _rebuild(self) -> None:
        d = self.doc
        if not d or not d.table:
            self.view = []
            self._heads, self._maps, self._widths = [], {}, []
            return
        self._heads, self._maps = self._layout()
        self._sync_visible()

        if self.show_sheet_col:
            items = self._search_all()
        else:
            t = d.table
            idx = list(range(t.nrows))
            if self.query:
                if self._blobs is None:
                    self._blobs = ["\x1f".join(r).lower() for r in t.rows]
                q = self.query.lower()
                idx = [i for i in idx if q in self._blobs[i]]
            offset = 2 if d.has_header else 1
            sheet = t.sheet or ""
            items = [(sheet, i + offset, t.rows[i]) for i in idx]

        # 先按列名对齐到统一表头，再排序：之后所有取单元格都只看"显示列"
        view = [(sheet, rowno, self._align(sheet, row)) for sheet, rowno, row in items]
        if self.sort_col is not None:
            col = self.sort_col
            view.sort(key=lambda e: _sort_key(e[2], col), reverse=self.sort_desc)
        self.view = view
        self._widths = self._compute_widths()

    def _search_all(self) -> list:
        d = self.doc
        q = self.query.lower()
        tables = d.all_tables()
        order = d.sheets or [d.table.sheet or ""]
        offset = 2 if d.has_header else 1
        out = []
        for name in order:
            tbl = tables.get(name)
            if tbl is None:
                continue
            for i, row in enumerate(tbl.rows):
                if q in "\x1f".join(row).lower():
                    out.append((name, i + offset, row))
        return out

    @staticmethod
    def _head_width(name: str) -> int:
        """只按标题长度算个兜底列宽（宽度还没算出来的情况）。"""
        chars = min(disp_len(str(name)), COL_CHARS_MAX)
        return int(min(max(chars * COL_CHAR_PX + COL_PAD_PX, COL_MIN_PX), COL_MAX_PX))

    def _compute_widths(self) -> list[int]:
        """列宽 = max(内容最长字符数, 标题) + 缓冲，再封顶。

        直接看当前视图的前 2000 行（已经按列名对齐过），宽窄跟着结果走。
        """
        sample = self.view[:2000]
        widths: list[int] = []
        for i, h in enumerate(self._heads):
            longest = disp_len(str(h))
            for _sheet, _no, row in sample:
                if i < len(row):
                    n = disp_len(row[i])
                    if n > longest:
                        longest = n
                        if longest >= COL_CHARS_MAX:
                            break
            chars = min(longest, COL_CHARS_MAX)
            widths.append(int(min(max(chars * COL_CHAR_PX + COL_PAD_PX, COL_MIN_PX),
                                  COL_MAX_PX)))
        return widths

    # -- 给页面的数据 ------------------------------------------------------ #
    def meta(self) -> dict:
        with self.lock:
            d = self.doc
            base = {
                "rev": self.rev,
                "error": self.error,
                "loading": self.loading,
                "recent": self.recent,
                "path": d.path if d else "",
                "name": d.name if d else "",
                **self.cfg.page(),
            }
            if not d or not d.table:
                base.update({"empty": True, "total": 0, "cols": [],
                             "lead": False, "leadName": False,
                             "sheets": [], "sheet": "",
                             "visible_cols": [], "all_cols": [], "query": "",
                             "layoutCols": 0,
                             "scope": self.scope, "sort": {"col": None, "desc": False},
                             "stats": None, "rowh": ROW_H,
                             "page": self.cfg.page_rows})
                return base

            t = d.table
            jump = self.show_jump_col
            lead = self.show_sheet_col
            heads = self._heads or list(t.headers)   # 兜底：还没重建过就用当前表
            cols = []
            if jump:
                # 带表名时按表名长短算宽，否则只放一个箭头就够
                if lead:
                    names = d.sheets or [t.sheet or ""]
                    longest = max([disp_len(n) for n in names] + [4])
                    w = max(int(min(longest * COL_CHAR_PX + 34, 280)), 90)
                else:
                    w = 56
                cols.append({"key": "sheet", "name": "前往", "w": w,
                             "kind": "sheet"})
            cols.append({"key": "rowno", "name": "#", "w": 56, "kind": "no"})
            for i in self.visible_cols:
                name = heads[i] if i < len(heads) else col_letter(i)
                arrow = ""
                if self.sort_col == i:
                    arrow = " ▼" if self.sort_desc else " ▲"
                cols.append({"key": "c%d" % i, "name": name + arrow,
                             "w": (self._widths[i] if i < len(self._widths)
                                   else self._head_width(name)),
                             "kind": "cell", "i": i})
            base.update({
                "empty": False,
                "total": len(self.view),
                "cols": cols,
                "lead": jump,
                "leadName": lead,
                "sheets": d.sheets,
                "sheet": t.sheet or "",
                "visible_cols": self.visible_cols,
                "all_cols": [{"i": i,
                              "name": (heads[i] if i < len(heads)
                                       else col_letter(i))}
                             for i in range(len(heads))],
                "layoutCols": len(heads),
                "query": self.query,
                "scope": self.scope,
                "sort": {"col": self.sort_col, "desc": self.sort_desc},
                "stats": {"nrows": t.nrows, "ncols": t.ncols,
                          "elapsed": round(t.elapsed, 2),
                          "truncated": t.truncated},
                "rowh": ROW_H,
                "page": self.cfg.page_rows,
            })
            return base

    def rows(self, start: int, count: int) -> dict:
        with self.lock:
            total = len(self.view)
            start = max(0, min(int(start), total))
            end = min(start + max(0, int(count)), total)
            cols = self.visible_cols
            lead = self.show_jump_col
            out = []
            for sheet, rowno, row in self.view[start:end]:
                cells = [row[i] if i < len(row) else "" for i in cols]
                out.append(([sheet, rowno] if lead else [rowno]) + cells)
            return {"start": start, "total": total, "rows": out}

    def _cell_text(self, i: int, k: int) -> str:
        """第 i 条视图记录里第 k 个显示列的文字。"""
        sheet, rowno, row = self.view[i]
        lead = 2 if self.show_jump_col else 1
        if k == 0 and self.show_jump_col:
            return sheet
        if k == lead - 1:
            return str(rowno)
        j = self.visible_cols[k - lead]
        return row[j] if j < len(row) else ""

    def range_tsv(self, r0: int, r1: int, c0: int, c1: int):
        with self.lock:
            total = len(self.view)
            if total == 0:
                return "", 0, 0
            r0, r1 = sorted((max(0, min(r0, total - 1)),
                             max(0, min(r1, total - 1))))
            c0, c1 = sorted((max(0, min(c0, self.n_disp_cols - 1)),
                             max(0, min(c1, self.n_disp_cols - 1))))
            lead = 2 if self.show_jump_col else 1
            cols = self.visible_cols
            lines = []
            for i in range(r0, r1 + 1):
                sheet, rowno, row = self.view[i]
                cells = ([sheet, str(rowno)] if self.show_jump_col
                         else [str(rowno)])
                cells += [row[j] if j < len(row) else "" for j in cols]
                lines.append("\t".join(cells[c0:c1 + 1]))
            return "\r\n".join(lines), len(lines), c1 - c0 + 1

    def cell(self, r: int, c: int) -> str:
        with self.lock:
            if not (0 <= r < len(self.view)):
                return ""
            if not (0 <= c < self.n_disp_cols):
                return ""
            return self._cell_text(r, c)
