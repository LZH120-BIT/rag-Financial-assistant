"""
PyMuPDF 能力边界探测脚本

目的：在动手写自研 OCR 管线前，摸清 PyMuPDF 各 API 对财报 PDF 的实际表现。
对 5 份代表性 PDF 探测：
1. find_tables 的表格识别准确度、多层表头处理、合并单元格表示
2. get_text("blocks") 的 bbox 坐标能否用来做多栏分列
3. get_text("dict") 提供哪些额外信息（字体、大小 -> 层级？）
4. 页面旋转 / 横向页在坐标系下的表现
5. 页眉页脚跨页重复检测的可行性

输出：research/pymupdf_probe_report.md + research/pymupdf_probe_raw.json
"""

from __future__ import annotations

import json
import statistics
import sys
import time
from collections import Counter
from pathlib import Path

import fitz  # PyMuPDF

ROOT = Path(__file__).resolve().parent.parent
GOLDEN = ROOT / "golden_set"
OUT_JSON = ROOT / "research" / "pymupdf_probe_raw.json"
OUT_MD = ROOT / "research" / "pymupdf_probe_report.md"

# 5 份代表性 PDF
SAMPLES = [
    ("SIMPLE",         "30f64d1043f4cb425eb636763580ae27094ffef1", "1-800-Flowers"),
    ("MULTIPAGE_TAB",  "ded965ce7e3e" , "Playtech plc"),
    ("MULTI_COLUMN",   "696ddc4c80fe" , "MC-heavy"),
    ("IMAGE_HEAVY",    "78c71282723c" , "Ritchie Bros"),
    ("LANDSCAPE",      "cc0fc5888b99" , "RWE AG"),
]


def find_pdf(prefix: str) -> Path | None:
    matches = list(GOLDEN.glob(f"{prefix}*.pdf"))
    return matches[0] if matches else None


def probe_tables(doc: fitz.Document, sample_pages: list[int]) -> dict:
    """探测 find_tables 表现。为省时只在采样页上跑。"""
    total_found = 0
    table_shapes: list[tuple[int, int]] = []
    header_rows_examples: list[list[str]] = []
    cell_none_ratio_samples: list[float] = []
    multi_header_hits = 0
    merged_cell_hits = 0
    sample_page_stats = []

    for pno in sample_pages:
        page = doc[pno]
        try:
            tables = page.find_tables()
        except Exception as e:
            sample_page_stats.append({"page": pno, "error": str(e)})
            continue
        tbls = list(tables)
        total_found += len(tbls)
        page_stat = {"page": pno, "tables_on_page": len(tbls), "shapes": []}
        for t in tbls[:3]:  # 每页最多看 3 个表
            try:
                data = t.extract()
            except Exception as e:
                page_stat["shapes"].append({"error": str(e)})
                continue
            if not data:
                continue
            rows, cols = len(data), max((len(r) for r in data), default=0)
            table_shapes.append((rows, cols))
            page_stat["shapes"].append({"rows": rows, "cols": cols})
            # 首行作为表头示例
            if len(header_rows_examples) < 8 and data[0]:
                header_rows_examples.append([str(c)[:30] if c else "" for c in data[0]])
            # None 比例（合并单元格通常表现为 None）
            flat = [c for row in data for c in row]
            if flat:
                none_ratio = sum(1 for c in flat if c is None or c == "") / len(flat)
                cell_none_ratio_samples.append(none_ratio)
                if none_ratio > 0.2:  # 高 None 率 => 疑似合并单元格
                    merged_cell_hits += 1
            # 多层表头启发式：前 2 行都短且无数字
            if len(data) >= 2:
                r0, r1 = data[0], data[1]
                def looks_header(row):
                    if not row: return False
                    strs = [str(c) for c in row if c]
                    if not strs: return False
                    has_digit = any(any(ch.isdigit() for ch in s) for s in strs)
                    return (not has_digit) and all(len(s) < 40 for s in strs)
                if looks_header(r0) and looks_header(r1):
                    multi_header_hits += 1
        sample_page_stats.append(page_stat)

    return {
        "sample_pages_probed": len(sample_pages),
        "total_tables_found": total_found,
        "shape_stats": {
            "rows_median": statistics.median(r for r, _ in table_shapes) if table_shapes else 0,
            "cols_median": statistics.median(c for _, c in table_shapes) if table_shapes else 0,
            "rows_max":    max((r for r, _ in table_shapes), default=0),
            "cols_max":    max((c for _, c in table_shapes), default=0),
        },
        "header_row_examples": header_rows_examples,
        "cell_none_ratio_avg": round(statistics.mean(cell_none_ratio_samples), 3) if cell_none_ratio_samples else 0,
        "multi_header_suspected_tables": multi_header_hits,
        "merged_cell_suspected_tables":  merged_cell_hits,
        "sample_page_stats": sample_page_stats[:5],  # 只留前 5 页详情
    }


def probe_columns(doc: fitz.Document, sample_pages: list[int]) -> dict:
    """探测 blocks 的 bbox 坐标能否区分左右栏。
    方法：把每页所有文本 block 的 x 中心点做直方图，如果有明显双峰 => 双栏。
    """
    dual_col_pages = 0
    single_col_pages = 0
    complex_pages = 0
    page_reports = []

    for pno in sample_pages:
        page = doc[pno]
        blocks = page.get_text("blocks")
        # blocks: (x0, y0, x1, y1, text, block_no, block_type)
        text_blocks = [b for b in blocks if len(b) >= 5 and b[4].strip() and b[6] == 0]
        if not text_blocks:
            continue
        width = page.rect.width
        x_centers = [(b[0] + b[2]) / 2 / width for b in text_blocks]  # 归一化到 0-1
        # 简单双峰检测：分左半 / 右半，看两侧块数
        left = sum(1 for x in x_centers if x < 0.45)
        right = sum(1 for x in x_centers if x > 0.55)
        middle = sum(1 for x in x_centers if 0.45 <= x <= 0.55)
        total = len(x_centers)
        if total == 0:
            continue
        left_ratio, right_ratio = left / total, right / total
        # 判定
        if middle / total > 0.5:
            single_col_pages += 1
            layout = "single"
        elif left_ratio > 0.3 and right_ratio > 0.3:
            dual_col_pages += 1
            layout = "dual"
        else:
            complex_pages += 1
            layout = "complex"
        page_reports.append({
            "page": pno,
            "layout": layout,
            "n_blocks": total,
            "left_ratio": round(left_ratio, 2),
            "right_ratio": round(right_ratio, 2),
            "middle_ratio": round(middle / total, 2),
        })

    return {
        "sample_pages_probed": len(sample_pages),
        "single_col_pages": single_col_pages,
        "dual_col_pages": dual_col_pages,
        "complex_pages": complex_pages,
        "page_reports": page_reports[:10],
    }


def probe_dict_structure(doc: fitz.Document, sample_pages: list[int]) -> dict:
    """探测 get_text("dict") 能提供的字体/大小信息，判断是否够用来识别标题层级。"""
    font_size_hist: Counter = Counter()
    font_name_hist: Counter = Counter()
    bold_lines = 0
    total_lines = 0
    for pno in sample_pages[:5]:  # 只看前 5 页省时
        page = doc[pno]
        d = page.get_text("dict")
        for blk in d.get("blocks", []):
            if blk.get("type") != 0:
                continue
            for line in blk.get("lines", []):
                total_lines += 1
                for span in line.get("spans", []):
                    size = round(span.get("size", 0), 1)
                    font_size_hist[size] += 1
                    font_name_hist[span.get("font", "")] += 1
                    flags = span.get("flags", 0)
                    # flags bit 4 = bold (per pymupdf)
                    if flags & 16:
                        bold_lines += 1
                        break
    return {
        "sample_pages_probed": min(5, len(sample_pages)),
        "font_size_top5": font_size_hist.most_common(5),
        "font_name_top5": font_name_hist.most_common(5),
        "bold_line_ratio": round(bold_lines / total_lines, 3) if total_lines else 0,
        "distinct_font_sizes": len(font_size_hist),
    }


def probe_rotation(doc: fitz.Document) -> dict:
    """统计所有页 rotation + landscape 情况。"""
    rot_hist = Counter()
    landscape_count = 0
    for page in doc:
        rot_hist[page.rotation] += 1
        if page.rect.width > page.rect.height:
            landscape_count += 1
    return {
        "total_pages": doc.page_count,
        "rotation_distribution": dict(rot_hist),
        "landscape_pages": landscape_count,
    }


def probe_header_footer(doc: fitz.Document, sample_pages: list[int]) -> dict:
    """跨页重复文本检测：抽 N 页，取每页顶部 / 底部区域文字，看是否高频重复。"""
    header_candidates: Counter = Counter()
    footer_candidates: Counter = Counter()
    for pno in sample_pages:
        page = doc[pno]
        h = page.rect.height
        for b in page.get_text("blocks"):
            if len(b) < 5:
                continue
            x0, y0, x1, y1, txt = b[0], b[1], b[2], b[3], b[4].strip()
            if not txt or len(txt) > 150:
                continue
            if y1 < h * 0.08:  # 顶部 8%
                header_candidates[txt] += 1
            elif y0 > h * 0.92:  # 底部 8%
                footer_candidates[txt] += 1
    return {
        "sample_pages_probed": len(sample_pages),
        "top_headers":  header_candidates.most_common(5),
        "top_footers":  footer_candidates.most_common(5),
    }


def choose_sample_pages(doc: fitz.Document, n: int = 20) -> list[int]:
    """均匀采样 N 页，最多 20 页。"""
    total = doc.page_count
    if total <= n:
        return list(range(total))
    step = total / n
    return [int(i * step) for i in range(n)]


def probe_one(pdf_path: Path, tag: str) -> dict:
    print(f"\n=== [{tag}] {pdf_path.name} ===", flush=True)
    t0 = time.time()
    doc = fitz.open(pdf_path)
    pages = choose_sample_pages(doc, 20)
    result = {
        "tag": tag,
        "file": pdf_path.name,
        "total_pages": doc.page_count,
        "sample_pages": pages,
        "rotation": probe_rotation(doc),
        "tables": probe_tables(doc, pages),
        "columns": probe_columns(doc, pages),
        "dict_struct": probe_dict_structure(doc, pages),
        "header_footer": probe_header_footer(doc, pages),
        "elapsed_sec": round(time.time() - t0, 2),
    }
    doc.close()
    print(f"    done in {result['elapsed_sec']}s", flush=True)
    return result


def main():
    results = []
    for tag, prefix, company in SAMPLES:
        pdf = find_pdf(prefix)
        if not pdf:
            print(f"[WARN] {tag} prefix {prefix} not found", file=sys.stderr)
            continue
        try:
            r = probe_one(pdf, tag)
            r["company"] = company
            results.append(r)
        except Exception as e:
            print(f"[ERROR] {tag}: {e}", file=sys.stderr)
            results.append({"tag": tag, "file": pdf.name, "error": str(e)})

    OUT_JSON.parent.mkdir(exist_ok=True)
    with open(OUT_JSON, "w") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)
    print(f"\nSaved raw: {OUT_JSON}")


if __name__ == "__main__":
    main()
