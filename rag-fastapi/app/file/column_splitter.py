"""
多栏分列器
==========

将 PyMuPDF `page.get_text("blocks")` 的输出按 x 坐标切分为左/右栏，
分别按 y 排序后拼接，从而修正双栏页"左右串行"的问题。

**通用规则**（版式几何层面，不依赖内容语义/语言）：
1. 只处理"清晰双栏"：左半 (<0.45) 和右半 (>0.55) 各占 ≥ 30%
2. 三栏以上 / 复杂版式 -> 降级为单栏（保守：不敢自动切，避免破坏正常页）
3. 单栏或 block 极少（<5 个）-> 直接返回 `page.get_text("text")`

设计取舍：
- 不自动识别栏数（避免过拟合）；只做二选一
- 表格区域（页面里被 find_tables 识别的 bbox）应先扣除，避免表内文字被切开
  -> 这一步交给上层 pipeline 做，本模块不负责
"""

from __future__ import annotations

import fitz

# 中间带（避免左右栏边缘 block 抖动误判）
LEFT_MAX = 0.45
RIGHT_MIN = 0.55
# 判定双栏需要每侧至少占 30%
DUAL_SIDE_MIN_RATIO = 0.30
# blocks 数量下限：小于这个数直接单栏 fallback
MIN_BLOCKS_FOR_SPLIT = 5


def _classify_layout(x_centers_normalized: list[float]) -> str:
    """返回 'single' / 'dual' / 'complex'。"""
    total = len(x_centers_normalized)
    if total < MIN_BLOCKS_FOR_SPLIT:
        return "single"
    left = sum(1 for x in x_centers_normalized if x < LEFT_MAX)
    right = sum(1 for x in x_centers_normalized if x > RIGHT_MIN)
    middle = total - left - right
    if middle / total > 0.5:
        return "single"
    if left / total >= DUAL_SIDE_MIN_RATIO and right / total >= DUAL_SIDE_MIN_RATIO:
        return "dual"
    return "complex"


def extract_page_text_by_columns(
    page: fitz.Page,
    excluded_bboxes: list[tuple[float, float, float, float]] | None = None,
) -> str:
    """按栏抽取页文本，返回单个字符串。

    Args:
        page: PyMuPDF 页对象
        excluded_bboxes: 需要跳过的 bbox（通常是表格区域），block 中心落在其中则跳过

    Returns:
        文本（左栏在前、右栏在后；单栏则按 y 顺序）
    """
    excluded_bboxes = excluded_bboxes or []

    raw_blocks = page.get_text("blocks")
    # blocks: (x0, y0, x1, y1, text, block_no, block_type)
    text_blocks = []
    for b in raw_blocks:
        if len(b) < 7 or b[6] != 0:  # 只要文本 block
            continue
        text = (b[4] or "").strip()
        if not text:
            continue
        x0, y0, x1, y1 = b[0], b[1], b[2], b[3]
        cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
        # 跳过 excluded 区域
        skip = False
        for ex in excluded_bboxes:
            if ex[0] <= cx <= ex[2] and ex[1] <= cy <= ex[3]:
                skip = True
                break
        if skip:
            continue
        text_blocks.append((x0, y0, x1, y1, text))

    if not text_blocks:
        return ""

    width = page.rect.width or 1.0
    x_centers_norm = [((b[0] + b[2]) / 2) / width for b in text_blocks]
    layout = _classify_layout(x_centers_norm)

    if layout != "dual":
        # 单栏或复杂 -> 按 y 顺序拼接
        text_blocks.sort(key=lambda b: (b[1], b[0]))
        return "\n".join(b[4] for b in text_blocks)

    # 双栏：切左右，各自按 y 排序
    left_col, right_col = [], []
    for b in text_blocks:
        cx = ((b[0] + b[2]) / 2) / width
        if cx < 0.5:
            left_col.append(b)
        else:
            right_col.append(b)
    left_col.sort(key=lambda b: b[1])
    right_col.sort(key=lambda b: b[1])

    parts = [b[4] for b in left_col] + [b[4] for b in right_col]
    return "\n".join(parts)


def detect_layout(page: fitz.Page) -> str:
    """外部工具：只判定布局，不抽取文本。返回 single/dual/complex。"""
    raw_blocks = page.get_text("blocks")
    text_blocks = [b for b in raw_blocks if len(b) >= 7 and b[6] == 0 and (b[4] or "").strip()]
    if not text_blocks:
        return "single"
    width = page.rect.width or 1.0
    x_centers_norm = [((b[0] + b[2]) / 2) / width for b in text_blocks]
    return _classify_layout(x_centers_norm)


# ---------- 自测 ----------

if __name__ == "__main__":
    import sys
    from pathlib import Path
    pdf = Path(sys.argv[1]) if len(sys.argv) > 1 else next(Path("golden_set").glob("696ddc4c80fe*.pdf"))
    doc = fitz.open(pdf)
    print(f"=== {pdf.name} ({doc.page_count} pages) ===")
    layout_counts = {"single": 0, "dual": 0, "complex": 0}
    scan = min(60, doc.page_count)
    for pno in range(scan):
        lay = detect_layout(doc[pno])
        layout_counts[lay] += 1
    print(f"first {scan} pages layout distribution: {layout_counts}")

    # 挑一页 dual 打印文本前 500 字
    for pno in range(scan):
        if detect_layout(doc[pno]) == "dual":
            txt = extract_page_text_by_columns(doc[pno])
            print(f"\n--- page {pno+1} (dual) first 500 chars ---")
            print(txt[:500])
            break
    doc.close()
