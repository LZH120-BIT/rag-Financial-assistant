"""
财报特殊内容真实频率调研

对 golden_set 抽样 20 份 PDF（覆盖不同规模/类型），扫描前 60 页，
统计 22 项待定项的**真实出现频率**，用数据代替猜测。

输出：research/content_frequency.json + content_frequency.md
"""

from __future__ import annotations

import json
import random
import re
import time
from collections import Counter, defaultdict
from pathlib import Path

import fitz

ROOT = Path(__file__).resolve().parent.parent
GOLDEN = ROOT / "golden_set"
OUT_JSON = ROOT / "research" / "content_frequency.json"

# 抽样：20 份 PDF，前 60 页
random.seed(42)
SAMPLE_SIZE = 20
PAGES_PER_PDF = 60


# ---------- 检测规则（尽量简单、确定性强） ----------

# 内容类
NUMBER_WITH_COMMA_RE = re.compile(r"\b\d{1,3}(?:,\d{3})+(?:\.\d+)?\b")     # 千分位数
PAREN_NEGATIVE_RE = re.compile(r"\(\s*[\d,]+(?:\.\d+)?\s*\)")               # 括号负数
YEAR_FISCAL_RE = re.compile(r"\b(FY\d{2,4}|fiscal\s*20\d{2})\b", re.I)      # FY22/fiscal 2022
YEAR_2DIGIT_RE = re.compile(r"\b\d{2}\s*(?:年|财年)\b")                     # 22年 / 22 财年
YEAR_4DIGIT_RE = re.compile(r"\b20\d{2}\b")                                 # 2022
DATE_ENGLISH_RE = re.compile(r"\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+\d{1,2},?\s+20\d{2}\b")
DATE_ISO_RE = re.compile(r"\b20\d{2}[-/.]\d{1,2}[-/.]\d{1,2}\b")

CURRENCY_SYMBOL_RE = re.compile(r"[\$€£¥￥]")
CURRENCY_CODE_RE = re.compile(r"\b(USD|EUR|GBP|CNY|JPY|HKD|AUD|CAD|CHF|SGD)\b")
UNIT_SUFFIX_RE = re.compile(r"\b\d[\d,\.]*\s*(million|billion|thousand|亿|万|千)\b", re.I)
UNIT_MSHORT_RE = re.compile(r"[€$£¥]m\b", re.I)  # €m / $m / £m

# 术语缩写（常见财务缩写；不追求全，只是采样看频率）
ACRONYM_RE = re.compile(r"\b(EBITDA|GAAP|IFRS|CAGR|CAPEX|OPEX|EPS|ROIC|ROE|ROA|FCF|WACC|MD&A|SG&A|R&D|COGS)\b")

# 文档结构关键词
MDA_RE = re.compile(r"\bManagement[’']?s?\s+Discussion\s+(and|&)\s+Analysis\b", re.I)
RISK_FACTORS_RE = re.compile(r"\bRisk\s+Factors\b", re.I)
AUDIT_REPORT_RE = re.compile(r"\b(Independent\s+Auditor|Auditor[’']?s?\s+Report|Report\s+of\s+Independent\s+Registered)\b", re.I)
APPENDIX_RE = re.compile(r"\b(Appendix|Exhibits?|Index)\b", re.I)

# 目录页
TOC_RE = re.compile(r"^\s*(Table\s+of\s+)?Contents\s*$", re.I | re.M)


def scan_pdf(pdf_path: Path) -> dict:
    """返回该 PDF 的各项统计。"""
    doc = fitz.open(pdf_path)
    n_pages = min(PAGES_PER_PDF, doc.page_count)

    stats = {
        "file": pdf_path.name,
        "total_pages": doc.page_count,
        "scanned_pages": n_pages,
        # 内容类计数
        "cnt_number_with_comma": 0,
        "cnt_paren_negative": 0,
        "cnt_year_fiscal": 0,
        "cnt_year_4digit": 0,
        "cnt_date_english": 0,
        "cnt_date_iso": 0,
        "cnt_currency_symbol": 0,
        "cnt_currency_code": 0,
        "cnt_unit_suffix": 0,
        "cnt_unit_mshort": 0,
        "cnt_acronym": 0,
        # 文档结构（是否出现）
        "has_mda": False,
        "has_risk_factors": False,
        "has_audit_report": False,
        "has_appendix": False,
        "has_toc": False,
        # 图像细节
        "images_ge_200x200": 0,
        "images_ge_4M_pixels": 0,
        "images_portrait_ratio_gt_1_5": 0,
        "pages_image_area_gt_50pct": 0,
        "pages_image_area_20_to_80_pct_with_text": 0,
        # 脚注编号（¹²³ 或 上标数字）— 用 unicode 上标
        "pages_with_superscript_footnote": 0,
        # 侧边栏候选：block 位于左 15% 或右 85% 且窄
        "pages_with_sidebar_candidate": 0,
    }

    for pno in range(n_pages):
        page = doc[pno]
        text = page.get_text("text") or ""
        page_w, page_h = page.rect.width, page.rect.height
        page_area = page_w * page_h if page_w and page_h else 1

        # 内容类
        stats["cnt_number_with_comma"] += len(NUMBER_WITH_COMMA_RE.findall(text))
        stats["cnt_paren_negative"] += len(PAREN_NEGATIVE_RE.findall(text))
        stats["cnt_year_fiscal"] += len(YEAR_FISCAL_RE.findall(text))
        stats["cnt_year_4digit"] += len(YEAR_4DIGIT_RE.findall(text))
        stats["cnt_date_english"] += len(DATE_ENGLISH_RE.findall(text))
        stats["cnt_date_iso"] += len(DATE_ISO_RE.findall(text))
        stats["cnt_currency_symbol"] += len(CURRENCY_SYMBOL_RE.findall(text))
        stats["cnt_currency_code"] += len(CURRENCY_CODE_RE.findall(text))
        stats["cnt_unit_suffix"] += len(UNIT_SUFFIX_RE.findall(text))
        stats["cnt_unit_mshort"] += len(UNIT_MSHORT_RE.findall(text))
        stats["cnt_acronym"] += len(ACRONYM_RE.findall(text))

        # 文档结构（只要一处出现就 True）
        if MDA_RE.search(text): stats["has_mda"] = True
        if RISK_FACTORS_RE.search(text): stats["has_risk_factors"] = True
        if AUDIT_REPORT_RE.search(text): stats["has_audit_report"] = True
        if APPENDIX_RE.search(text): stats["has_appendix"] = True
        if TOC_RE.search(text): stats["has_toc"] = True

        # 上标脚注（unicode 上标数字）
        if any(ch in text for ch in "¹²³⁴⁵⁶⁷⁸⁹"):
            stats["pages_with_superscript_footnote"] += 1

        # 图像细节
        try:
            img_list = page.get_images(full=True)
        except Exception:
            img_list = []
        page_image_area = 0
        for img in img_list:
            xref = img[0]
            try:
                info = doc.extract_image(xref)
                w, h = info.get("width", 0), info.get("height", 0)
            except Exception:
                continue
            if w >= 200 and h >= 200:
                stats["images_ge_200x200"] += 1
            if w * h >= 4_000_000:
                stats["images_ge_4M_pixels"] += 1
            if h > 0 and h / max(w, 1) > 1.5:
                stats["images_portrait_ratio_gt_1_5"] += 1
            # 图像在页面显示的 bbox（get_image_rects 是新 API）
            try:
                rects = page.get_image_rects(xref)
                for r in rects:
                    page_image_area += r.width * r.height
            except Exception:
                pass
        img_area_ratio = page_image_area / page_area if page_area > 0 else 0
        text_length = len(text.strip())
        if img_area_ratio > 0.5:
            stats["pages_image_area_gt_50pct"] += 1
        if 0.2 <= img_area_ratio <= 0.8 and text_length >= 50:
            stats["pages_image_area_20_to_80_pct_with_text"] += 1

        # 侧边栏候选：一个窄 block 位于左 15% 或右 85%
        try:
            blocks = page.get_text("blocks")
        except Exception:
            blocks = []
        has_sidebar = False
        for b in blocks:
            if len(b) < 7 or b[6] != 0:
                continue
            x0, x1 = b[0], b[2]
            width_ratio = (x1 - x0) / page_w if page_w > 0 else 0
            if width_ratio > 0 and width_ratio < 0.15:
                cx = (x0 + x1) / 2
                cxr = cx / page_w if page_w > 0 else 0
                if cxr < 0.15 or cxr > 0.85:
                    has_sidebar = True
                    break
        if has_sidebar:
            stats["pages_with_sidebar_candidate"] += 1

    doc.close()
    return stats


def main():
    all_pdfs = sorted(GOLDEN.glob("*.pdf"))
    sample = random.sample(all_pdfs, min(SAMPLE_SIZE, len(all_pdfs)))
    print(f"Sampling {len(sample)} PDFs from {len(all_pdfs)} total")

    results = []
    t0 = time.time()
    for i, pdf in enumerate(sample, 1):
        try:
            r = scan_pdf(pdf)
            results.append(r)
            print(f"  [{i:2d}/{len(sample)}] {pdf.name[:40]:40s} pages={r['scanned_pages']}", flush=True)
        except Exception as e:
            print(f"  [{i:2d}/{len(sample)}] ERROR {pdf.name}: {e}")

    # 聚合
    n = len(results)
    total_pages = sum(r["scanned_pages"] for r in results)
    aggregate = {
        "n_pdfs": n,
        "total_pages_scanned": total_pages,
        "avg_pages_per_pdf": round(total_pages / n, 1),

        # 内容类：每 100 页平均命中数
        "avg_per_100p": {
            k[4:]: round(sum(r[k] for r in results) / total_pages * 100, 1)
            for k in results[0] if k.startswith("cnt_")
        },
        # 文档结构：多少份 PDF 出现
        "pct_pdfs_with": {
            k[4:]: round(sum(1 for r in results if r[k]) / n * 100, 1)
            for k in results[0] if k.startswith("has_")
        },
        # 图像细节
        "images_summary": {
            "avg_ge_200x200_per_pdf": round(sum(r["images_ge_200x200"] for r in results) / n, 1),
            "pct_pdfs_with_ge_4M_pixels_img": round(sum(1 for r in results if r["images_ge_4M_pixels"] > 0) / n * 100, 1),
            "pct_pdfs_with_portrait_img": round(sum(1 for r in results if r["images_portrait_ratio_gt_1_5"] > 0) / n * 100, 1),
            "avg_pages_img_area_gt_50pct_per_pdf": round(sum(r["pages_image_area_gt_50pct"] for r in results) / n, 1),
            "avg_pages_img_area_20_80_with_text_per_pdf": round(sum(r["pages_image_area_20_to_80_pct_with_text"] for r in results) / n, 1),
        },
        # 版式细节
        "layout_summary": {
            "pct_pdfs_with_superscript_footnote": round(sum(1 for r in results if r["pages_with_superscript_footnote"] > 0) / n * 100, 1),
            "avg_footnote_pages_per_pdf": round(sum(r["pages_with_superscript_footnote"] for r in results) / n, 1),
            "pct_pdfs_with_sidebar_candidate": round(sum(1 for r in results if r["pages_with_sidebar_candidate"] > 0) / n * 100, 1),
            "avg_sidebar_pages_per_pdf": round(sum(r["pages_with_sidebar_candidate"] for r in results) / n, 1),
        },
        "elapsed_sec": round(time.time() - t0, 1),
    }

    out = {"aggregate": aggregate, "per_pdf": results}
    OUT_JSON.parent.mkdir(exist_ok=True)
    with open(OUT_JSON, "w") as f:
        json.dump(out, f, indent=2, ensure_ascii=False)
    print(f"\nSaved: {OUT_JSON}")
    print(f"\nElapsed: {aggregate['elapsed_sec']}s")
    print(f"\n=== AGGREGATE ===")
    print(json.dumps(aggregate, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
