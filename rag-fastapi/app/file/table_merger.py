"""
跨页表拼接器
============

将 `table_extractor.ExtractedTable` 列表按"版式几何"判据拼接成跨页表。

**通用判据**（不依赖内容语义/语言/行业）：
1. **相邻页**：page_no 差 = 1
2. **列数一致**：n_cols 相等
3. **列 x 中心对齐**：每列 x 中心差异 < 页宽 * 阈值(3%)
4. **下页表位于页顶**：bbox.y0 < 页高 * 20%
5. **上页表位于页底**：bbox.y1 > 页高 * 80%

**表头去重**：下页首行如果与上页表头行高度相似 (Jaccard > 0.8) → 跳过

设计取舍：
- 只做"相邻两页"链式拼接，可传递到 N 页
- 不依赖任何内容语义（数字/币种/关键词），保持跨语言通用
- 拼接失败时保留原始独立表，不删数据
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable

from .table_extractor import ExtractedTable


# ---------- 配置 ----------

# 列 x 中心对齐容差（相对页宽）
COL_ALIGN_TOLERANCE = 0.03
# 判定"页顶"的 y0 阈值（相对页高）
TOP_THRESHOLD = 0.20
# 判定"页底"的 y1 阈值（相对页高）
BOTTOM_THRESHOLD = 0.80
# 表头行 Jaccard 相似度阈值（>= 视为重复表头）
HEADER_JACCARD = 0.8
# 拼接后单表行数上限（防爆内存）
MAX_MERGED_ROWS = 500


# ---------- 拼接后数据结构 ----------

@dataclass
class MergedTable:
    """跨页拼接后的表。若未拼接（单页表），source_pages 只含一个元素。"""
    source_pages: list[int]         # 参与拼接的 0-based 页号列表
    n_rows: int
    n_cols: int
    header_rows: int
    rows: list[list[str]]           # 已去重表头 + 拼接后的完整数据
    was_merged: bool = False        # 是否跨页拼接（True 表示 >=2 页）

    def to_markdown(self) -> str:
        # 复用 ExtractedTable 的 markdown 序列化（构造一个临时实例）
        tmp = ExtractedTable(
            page_no=self.source_pages[0],
            bbox=(0, 0, 0, 0),
            n_rows=self.n_rows,
            n_cols=self.n_cols,
            header_rows=self.header_rows,
            rows=self.rows,
        )
        md = tmp.to_markdown()
        if self.was_merged:
            pages = ", ".join(str(p + 1) for p in self.source_pages)  # 1-based 展示
            md = f"<!-- merged table across pages {pages} -->\n" + md
        return md


# ---------- 内部工具 ----------

def _row_jaccard(a: list[str], b: list[str]) -> float:
    """两行的 Jaccard 相似度（按去空白后的单元格集合）。"""
    sa = {c.strip() for c in a if c and c.strip()}
    sb = {c.strip() for c in b if c and c.strip()}
    if not sa and not sb:
        return 1.0
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


def _columns_aligned(
    a: ExtractedTable, b: ExtractedTable, page_width: float
) -> bool:
    """两表的列 x 中心是否对齐。"""
    if a.n_cols != b.n_cols:
        return False
    if not a.col_x_centers or not b.col_x_centers:
        # 没有 x 中心数据 -> 退化为只看列数，返回 True 让上层继续判别
        return True
    if len(a.col_x_centers) != len(b.col_x_centers):
        return False
    tol = page_width * COL_ALIGN_TOLERANCE
    for xa, xb in zip(a.col_x_centers, b.col_x_centers):
        if abs(xa - xb) > tol:
            return False
    return True


def _is_continuation(
    prev: ExtractedTable,
    curr: ExtractedTable,
    prev_page_height: float,
    curr_page_height: float,
    page_width: float,
) -> bool:
    """判断 curr 是否是 prev 的跨页延续。"""
    # 1. 相邻页
    if curr.page_no - prev.page_no != 1:
        return False
    # 2. 列数一致
    if prev.n_cols != curr.n_cols:
        return False
    # 3. 上页表位于页底
    if prev.bbox[3] < prev_page_height * BOTTOM_THRESHOLD:
        return False
    # 4. 下页表位于页顶
    if curr.bbox[1] > curr_page_height * TOP_THRESHOLD:
        return False
    # 5. 列 x 中心对齐
    if not _columns_aligned(prev, curr, page_width):
        return False
    return True


def _strip_repeated_header(
    prev: ExtractedTable, curr: ExtractedTable
) -> list[list[str]]:
    """如果 curr 首行与 prev 表头相似 → 跳过 curr 首若干行。
    Returns: curr 应拼接的行（不含被识别为"重复表头"的部分）
    """
    if not curr.rows:
        return []
    # 尝试和 prev 的每一头行比对，如果 curr 前 K 行 = prev 前 K 头行，则跳过 K 行
    max_strip = min(prev.header_rows, len(curr.rows))
    strip = 0
    for k in range(1, max_strip + 1):
        # curr 前 k 行 vs prev 前 k 头行
        sims = [_row_jaccard(prev.rows[i], curr.rows[i]) for i in range(k)]
        if all(s >= HEADER_JACCARD for s in sims):
            strip = k
    return curr.rows[strip:]


# ---------- 主入口 ----------

def merge_tables_across_pages(
    tables: Iterable[ExtractedTable],
    page_dims: dict[int, tuple[float, float]],
) -> list[MergedTable]:
    """按跨页拼接算法合并表格列表。

    Args:
        tables: 所有页所有表的扁平列表，按 (page_no, bbox.y0) 已排序或未排序均可
        page_dims: {page_no: (width, height)} 每页尺寸，用于阈值判定

    Returns:
        list[MergedTable]，单表和跨页表混合
    """
    # 排序：先按 page_no，再按 bbox.y0（同页多表按位置从上到下）
    sorted_tables = sorted(tables, key=lambda t: (t.page_no, t.bbox[1]))

    result: list[MergedTable] = []
    current: MergedTable | None = None
    prev_table: ExtractedTable | None = None

    for t in sorted_tables:
        page_w, page_h = page_dims.get(t.page_no, (595, 842))  # A4 兜底
        if prev_table is not None:
            _, prev_h = page_dims.get(prev_table.page_no, (595, 842))
            if _is_continuation(prev_table, t, prev_h, page_h, page_w):
                # 拼接到 current
                assert current is not None
                new_rows = _strip_repeated_header(prev_table, t)
                if current.n_rows + len(new_rows) <= MAX_MERGED_ROWS:
                    current.rows.extend(new_rows)
                    current.n_rows = len(current.rows)
                    current.source_pages.append(t.page_no)
                    current.was_merged = True
                    prev_table = t
                    continue
                # 超上限 -> 结束当前，开新表
        # 不是延续 -> 收尾旧 current，开新
        if current is not None:
            result.append(current)
        current = MergedTable(
            source_pages=[t.page_no],
            n_rows=t.n_rows,
            n_cols=t.n_cols,
            header_rows=t.header_rows,
            rows=[row[:] for row in t.rows],  # 深拷贝行
            was_merged=False,
        )
        prev_table = t

    if current is not None:
        result.append(current)

    return result


# ---------- 自测 ----------

if __name__ == "__main__":
    import sys
    import fitz
    from pathlib import Path

    # 需要用 python -m 才能导入相对包
    if __package__ is None or __package__ == "":
        sys.path.insert(0, str(Path(__file__).parent.parent.parent))
        from app.file.table_extractor import extract_tables_from_page
        from app.file.table_merger import merge_tables_across_pages as _merge
    else:
        from .table_extractor import extract_tables_from_page
        _merge = merge_tables_across_pages

    pdf = Path(sys.argv[1]) if len(sys.argv) > 1 else next(Path("golden_set").glob("ded965ce7e3e*.pdf"))
    doc = fitz.open(pdf)
    print(f"=== {pdf.name} ({doc.page_count} pages) ===")
    all_tables: list[ExtractedTable] = []
    page_dims: dict[int, tuple[float, float]] = {}
    scan_pages = min(80, doc.page_count)
    for pno in range(scan_pages):
        page = doc[pno]
        page_dims[pno] = (page.rect.width, page.rect.height)
        all_tables.extend(extract_tables_from_page(page, pno))
    doc.close()

    merged = _merge(all_tables, page_dims)
    n_merged = sum(1 for m in merged if m.was_merged)
    print(f"raw tables (after pseudo filter): {len(all_tables)}")
    print(f"merged tables: {len(merged)} (of which {n_merged} are cross-page)")
    for m in merged[:5]:
        if m.was_merged:
            print(f"\n--- MERGED pages={[p+1 for p in m.source_pages]} shape={m.n_rows}x{m.n_cols} ---")
            print(m.to_markdown()[:600])
