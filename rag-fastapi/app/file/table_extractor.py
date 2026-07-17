"""
表格增强抽取器
===============

在 PyMuPDF `page.find_tables()` 基础上做三件事：
1. **None 填充**（合并单元格向左/向上继承）
2. **伪表过滤**（把段落误检为 1×N 表的丢弃）
3. **单元格清洗**（strip、去换行、None -> ""）

输出统一的 `ExtractedTable` 数据类，供后续跨页拼接和 markdown 序列化使用。

设计取舍：
- 只处理原生矢量表格；图片形式的表交给 known limitation
- 保留 bbox 以便跨页拼接判断列对齐
- 保留表头行数推断（用于跨页时判定"下页首行是否为表头重复"）
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

import fitz  # PyMuPDF


# ---------- 数据结构 ----------

@dataclass
class ExtractedTable:
    """一个抽取出来的表格。"""
    page_no: int                         # 0-based
    bbox: tuple[float, float, float, float]  # x0,y0,x1,y1
    n_rows: int
    n_cols: int
    header_rows: int                     # 推断的表头行数 (>=1)
    rows: list[list[str]]                # 已经 None 填充 + 清洗后的二维文本
    col_x_centers: list[float] = field(default_factory=list)  # 每列的 x 中心，用于跨页对齐
    is_suspected: bool = False           # 是否疑似伪表（保留但打标）
    reason_dropped: str | None = None    # 若被过滤，说明原因

    def to_markdown(self) -> str:
        """转 markdown 表格。表头使用推断的 header_rows。"""
        if not self.rows or not self.n_cols:
            return ""
        # 表头：把 header_rows 行合并为单行；去掉每列内部重复（合并单元格展开后常重复）
        header_cells = []
        for c in range(self.n_cols):
            seen = []
            for r in range(self.header_rows):
                if r < len(self.rows) and c < len(self.rows[r]):
                    v = self.rows[r][c]
                    if v and v not in seen:
                        seen.append(v)
            header_cells.append(" / ".join(seen) if seen else " ")
        body = self.rows[self.header_rows:]
        lines = ["| " + " | ".join(header_cells) + " |",
                 "|" + "|".join(["---"] * self.n_cols) + "|"]
        for row in body:
            padded = row + [""] * (self.n_cols - len(row))
            lines.append("| " + " | ".join(_md_escape(c) for c in padded[:self.n_cols]) + " |")
        return "\n".join(lines)


# ---------- 内部辅助 ----------

_WS_RE = re.compile(r"\s+")


def _md_escape(cell: str) -> str:
    """Markdown 表格单元格里 | 和换行都要处理。"""
    if not cell:
        return " "
    s = cell.replace("|", "\\|").replace("\n", " ")
    return _WS_RE.sub(" ", s).strip() or " "


def _clean_cell(v: Any) -> str:
    """None -> '', 数字 -> str, 去多余空白。保留数字格式（含 $ , % 等）。"""
    if v is None:
        return ""
    s = str(v).strip()
    return _WS_RE.sub(" ", s)


def _drop_empty_columns(
    rows: list[list[str]], header_rows: int, n_cols: int
) -> tuple[list[list[str]], int]:
    """删除**数据区完全为空**的列（header 区可以有值，因为可能是合并单元格的空表头）。
    这是财报 PyMuPDF 抽表最常见的 artifact：一个数据 group 被拆成
    ['', '', '$', '1,234'] 这样 4 列，前两列在数据区都是空。
    """
    if n_cols < 2 or not rows:
        return rows, n_cols
    data_rows = rows[header_rows:]
    if not data_rows:
        return rows, n_cols

    cols_to_drop: list[int] = []
    for c in range(n_cols):
        data_cells = [r[c] if c < len(r) else "" for r in data_rows]
        if not any(x.strip() for x in data_cells if x):
            cols_to_drop.append(c)

    if not cols_to_drop:
        return rows, n_cols

    new_rows = [
        [row[c] if c < len(row) else "" for c in range(n_cols) if c not in cols_to_drop]
        for row in rows
    ]
    return new_rows, n_cols - len(cols_to_drop)


def _fill_none_forward(raw: list[list[Any]]) -> list[list[str]]:
    """合并单元格向左继承。PyMuPDF find_tables 用 **None** 表示合并单元格的延续格；
    empty string '' 表示"这里就是空"。因此：
      - None -> 继承同行左边最近的非 None cell 内容（合并单元格向左展开）
      - ''   -> 保留原样
    这样 header 行 ['A', None, None] 会被展开为 ['A', 'A', 'A']，
    再传给 to_markdown 时无需重复拼接。
    """
    if not raw:
        return []
    out: list[list[str]] = []
    for row in raw:
        new_row: list[str] = []
        last_non_none = ""
        for v in row:
            if v is None:
                # 合并单元格延续 -> 继承左边
                new_row.append(last_non_none)
            else:
                cleaned = _clean_cell(v)
                new_row.append(cleaned)
                last_non_none = cleaned
        out.append(new_row)
    return out


# 财报"货币符号独占列"合并
# 第一性原理：真数据表不会出现"整列都是 $/€/£/¥ 或空"的情况；
# 出现这种列一定是拆列 artifact，把它 merge 进右邻列即可。
_CURRENCY_ONLY_RE = re.compile(r"^[\$€£¥₹]+$")


def _is_currency_or_empty(cell: str) -> bool:
    if not cell:
        return True
    return bool(_CURRENCY_ONLY_RE.match(cell.strip()))


def _merge_currency_columns(
    rows: list[list[str]], header_rows: int, n_cols: int
) -> tuple[list[list[str]], int]:
    """财报常见 pattern：`$` 独占一列 + 数字独占一列 -> 合并为一列
    规则：
      1. 只考虑**数据区**列内容（header 区不做判断，避免把 "USD" 类列名当货币符号）
      2. 一列被判"货币符号列"的条件：数据区非空 cell 全部满足 is_currency_or_empty，
         且该列数据区非空 cell 数 ≥ 1（避免整列全空的假阳性）
      3. 合并方向：向右合并到相邻数据列。如果它是最后一列，向左合并。
      4. 合并后表头也要对齐（把符号列的表头 prepend 到目标列表头，通常都是空所以无影响）
    """
    if n_cols < 2 or not rows:
        return rows, n_cols
    data_rows = rows[header_rows:]
    if not data_rows:
        return rows, n_cols

    cols_to_drop: list[int] = []
    merge_target: dict[int, int] = {}   # currency_col -> target_col

    for c in range(n_cols):
        col_cells = [r[c] if c < len(r) else "" for r in data_rows]
        non_empty = [x for x in col_cells if x]
        if not non_empty:
            continue
        if not all(_is_currency_or_empty(x) for x in non_empty):
            continue
        # 找相邻数据列：优先右邻（c+1），否则左邻（c-1）
        target = None
        if c + 1 < n_cols and (c + 1) not in cols_to_drop:
            target = c + 1
        elif c - 1 >= 0 and (c - 1) not in cols_to_drop:
            target = c - 1
        if target is None:
            continue
        cols_to_drop.append(c)
        merge_target[c] = target

    if not cols_to_drop:
        return rows, n_cols

    # 执行合并
    new_rows: list[list[str]] = []
    for r_idx, row in enumerate(rows):
        row = row + [""] * (n_cols - len(row))
        # 只在数据区做 concat；header 区只 drop（避免多层表头同值被拼成 doubled）
        if r_idx >= header_rows:
            for src, tgt in merge_target.items():
                sym = row[src].strip() if row[src] else ""
                if sym:
                    row[tgt] = f"{sym}{row[tgt].lstrip()}" if row[tgt] else sym
        # 再删掉符号列
        new_row = [row[c] for c in range(n_cols) if c not in cols_to_drop]
        new_rows.append(new_row)

    return new_rows, n_cols - len(cols_to_drop)


def _fill_header_down(rows: list[list[str]], header_rows: int) -> list[list[str]]:
    """表头区专用：把 None/'' 向下继承（多层表头很常见的表现）。
    例：
        [['2022', '', '2021', ''],
         ['USD', 'CNY', 'USD', 'CNY']]
    应变为：
        [['2022', '2022', '2021', '2021'],
         ['USD', 'CNY', 'USD', 'CNY']]
    """
    if header_rows <= 1 or not rows:
        return rows
    for r in range(header_rows):
        for c in range(len(rows[r])):
            if not rows[r][c]:
                # 向左找同行非空
                for cc in range(c - 1, -1, -1):
                    if rows[r][cc]:
                        rows[r][c] = rows[r][cc]
                        break
    return rows


def _row_num_count(row: list[str]) -> int:
    """一行里"财务数字单元格"的个数。用于表头行数推断。

    第一性原理：财务数字 ≠ 4 位年份。判据必须包含以下 pattern 之一：
      - 千分位 (含逗号且后跟 3 位数字)      1,234
      - 小数点数字                          12.5
      - 带货币或百分号                       $, %
      - 括号负数                             (1,234)
      - 破折号（表格空数值）                 —, -, –
      - 3+ 位纯整数，但排除 4 位年份 (1900-2099)
    """
    n = 0
    for c in row:
        if not c:
            continue
        s = c.strip()
        if "$" in s or "%" in s or "€" in s or "£" in s or "¥" in s:
            n += 1; continue
        if re.search(r"\d,\d{3}", s):        # 千分位
            n += 1; continue
        if re.search(r"\d\.\d", s):          # 小数
            n += 1; continue
        if re.search(r"\(\d", s):            # 括号负数
            n += 1; continue
        if s in ("—", "–", "-"):             # 表格空数值符号
            n += 1; continue
        if re.fullmatch(r"\d{3,}", s):
            # 3+ 位纯整数，排除 4 位年份
            if re.fullmatch(r"(19|20)\d{2}", s):
                continue
            n += 1
    return n


def _infer_header_rows(raw: list[list[str]]) -> int:
    """推断表头行数。
    启发式：从第 0 行往下，遇到"数据行"（>=2 个数字单元格）就停。
    - 之前的数据行数就是表头行数（至少 1）
    - 都不是数据行 -> 保守取 1 行，避免把整表当表头
    - 上限 2 行（多层表头 >2 层的情况非常少）
    """
    for i, row in enumerate(raw[:3]):
        if _row_num_count(row) >= 2:
            return max(1, i)
    return 1


def _is_pseudo_table(raw: list[list[str]], n_rows: int, n_cols: int) -> tuple[bool, str]:
    """伪表过滤（v1 通用版，基于第一性原理，不针对特定 badcase）。

    核心假设：**真表 = 数据网格，伪表 = 文本网格**
    只用两条稳健规则，不加数字/财务符号等启发式（等 Golden Set 出真 badcase 再加）：

    1. **结构退化**：0 行 / 0 列 / 单列 -> 不是表
    2. **密度过低**：非空 cell < 15% -> 稀疏散块，不是表
    3. **单元格长度中位数过大**：真表 cell 通常短；文本网格（多栏正文/图注/目录）cell 长
       - 用中位数抗离群点，阈值取 60 字符（约 10 个英文单词，或 30 个汉字）
       - 只在 n_rows*n_cols >= 4 时启用，避免小表误伤

    这里刻意不加：
    - "数字占比" -> 对纯文本参考表（如章节列表）会误伤
    - "财务符号" -> 会过拟合训练样本
    - "long_cells 占比" -> 阈值难定，比中位数不稳
    """
    if n_rows == 0 or n_cols == 0:
        return True, "empty"
    if n_cols == 1:
        return True, "single_column"

    flat = [c for row in raw for c in row]
    non_empty = [c for c in flat if c]
    if not non_empty:
        return True, "empty_cells"

    density = len(non_empty) / (n_rows * n_cols)
    if density < 0.15:
        return True, f"low_density_{density:.2f}"

    # 中位数长度（只统计非空 cell）
    if n_rows * n_cols >= 4:
        lengths = sorted(len(c) for c in non_empty)
        median_len = lengths[len(lengths) // 2]
        if median_len > 60:
            return True, f"text_grid_median_len_{median_len}"

    return False, ""


def _col_x_centers_from_bbox(t) -> list[float]:
    """从 fitz Table 的 cells 里推断每列的 x 中心。
    用于跨页表判断"列结构是否对齐"。
    fitz.Table 对象暴露 .cells 属性（bbox 列表）。
    """
    try:
        cells = t.cells  # list of bbox tuples
        # 按行 y 分组，取第一行 cells 的 x 中心
        if not cells:
            return []
        # cells 顺序是 row-major，取列数
        n_cols = len(t.header.names) if hasattr(t, "header") and t.header else 0
        # 兜底：取第一行
        first_row = cells[:n_cols] if n_cols else cells[: max(1, len(cells) // max(1, len(t.rows)))]
        return [(c[0] + c[2]) / 2 for c in first_row if c]
    except Exception:
        return []


# ---------- 主入口 ----------

def extract_tables_from_page(
    page: fitz.Page,
    page_no: int,
    drop_pseudo: bool = True,
) -> list[ExtractedTable]:
    """抽取单页所有表格，做 None 填充、伪表过滤、表头推断。

    Args:
        page: PyMuPDF 页对象
        page_no: 0-based 页号（用于跨页拼接时定位）
        drop_pseudo: True 则直接丢弃伪表；False 保留但打 is_suspected=True

    Returns:
        list[ExtractedTable]
    """
    result: list[ExtractedTable] = []
    try:
        finder = page.find_tables()
        tables = list(finder)
    except Exception:
        return result

    page_h = page.rect.height

    for t in tables:
        try:
            raw = t.extract()
        except Exception:
            continue
        if not raw:
            continue

        n_rows = len(raw)
        n_cols = max((len(r) for r in raw), default=0)
        if n_cols == 0:
            continue

        # 补齐每行到 n_cols
        raw = [row + [""] * (n_cols - len(row)) for row in raw]

        cleaned = _fill_none_forward(raw)
        header_rows = _infer_header_rows(cleaned)
        cleaned = _fill_header_down(cleaned, header_rows)

        # 财报 PyMuPDF 抽表 artifact 清理（顺序不能反）：
        #   1. 先删数据区完全空列（多是"$ + 数字"被塞进 4 格造成的假列）
        #   2. 再合并"数据区全是货币符号"的独占列到相邻数据列
        cleaned, n_cols = _drop_empty_columns(cleaned, header_rows, n_cols)
        cleaned, n_cols = _merge_currency_columns(cleaned, header_rows, n_cols)

        bbox = tuple(t.bbox) if hasattr(t, "bbox") and t.bbox else (0, 0, 0, 0)

        # 位置类伪表：整个表都在页眉区（y1 < 8%）或页脚区（y0 > 92%）
        # 这是通用规则：真数据表不会只出现在这些区域
        y0, y1 = bbox[1], bbox[3]
        pos_reason = None
        if page_h > 0:
            if y1 < page_h * 0.08:
                pos_reason = "in_header_area"
            elif y0 > page_h * 0.92:
                pos_reason = "in_footer_area"

        if pos_reason:
            is_pseudo, reason = True, pos_reason
        else:
            is_pseudo, reason = _is_pseudo_table(cleaned, n_rows, n_cols)

        if is_pseudo and drop_pseudo:
            continue

        result.append(
            ExtractedTable(
                page_no=page_no,
                bbox=bbox,  # type: ignore[arg-type]
                n_rows=n_rows,
                n_cols=n_cols,
                header_rows=header_rows,
                rows=cleaned,
                col_x_centers=_col_x_centers_from_bbox(t),
                is_suspected=is_pseudo,
                reason_dropped=reason if is_pseudo else None,
            )
        )
    return result


# ---------- 自测 ----------

if __name__ == "__main__":
    import sys
    from pathlib import Path

    if len(sys.argv) < 2:
        # 默认拿 SIMPLE 样本
        pdf = next(Path("golden_set").glob("30f64d1043f4*.pdf"))
    else:
        pdf = Path(sys.argv[1])
    doc = fitz.open(pdf)
    print(f"=== {pdf.name} ({doc.page_count} pages) ===")
    total_kept = total_dropped = 0
    for pno in range(min(30, doc.page_count)):
        tabs = extract_tables_from_page(doc[pno], pno, drop_pseudo=False)
        for t in tabs:
            if t.is_suspected:
                total_dropped += 1
            else:
                total_kept += 1
                if total_kept <= 3:  # 前 3 个 dump 出来看看
                    print(f"\n--- page {pno} table {t.n_rows}x{t.n_cols} header_rows={t.header_rows} ---")
                    print(t.to_markdown()[:500])
    print(f"\nkept={total_kept} dropped={total_dropped}")
    doc.close()
