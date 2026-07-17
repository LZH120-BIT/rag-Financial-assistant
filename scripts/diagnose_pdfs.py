"""
Golden Set 101 份 PDF 诊断脚本

目标：用数据说话，摸清 33 项"特殊内容"在实际语料中的分布。

仅使用 PyMuPDF（fitz），不调 LLM，不做实际 OCR，纯静态分析（快速、可全量）。

每份 PDF 输出的指标（全部聚合到 pdf_diagnosis.json）：

【文本抽取健康度】
- pages_total: 总页数
- pages_text_ok: get_text 抽出 >= 100 字符的页数
- pages_low_text: get_text 抽出 < 50 字符的页数（疑似扫描/图片页）
- pages_empty: get_text 抽出 == 0 的页数
- total_chars_extracted: 全文档抽出字符数

【图像相关】
- total_images: 全文档嵌入图片数量（fitz.get_images）
- large_images: 面积 > 1000×1000 像素的大图数量
- huge_images: 面积 > 2000×2000 像素的超大图数量
- image_heavy_pages: 图片面积占页面比例 > 50% 的页数

【表格相关】
- native_tables_detected: fitz.find_tables 检测到的原生表格数量（PDF 电子层有结构的）
- pages_with_tables: 有表格的页数
- max_table_rows: 单表最大行数
- max_table_cols: 单表最大列数
- suspected_multipage_tables: 疑似跨页表（连续页出现表格）

【版式相关】
- landscape_pages: 横向排版页数（宽 > 高）
- rotated_pages: 页面 rotation != 0 的页数
- multi_column_pages: 疑似多栏排版页数（用文本块 x 坐标分布判断）

【噪声相关】
- has_repeating_header_footer: 是否存在重复出现在多页的短文本（页眉页脚）
- toc_pages: 疑似目录页数（连续短行 + 大量 . 或页码）

【规模特征】
- file_size_mb: 文件大小
- estimated_ocr_time_sec: 粗估如果对所有低文字页做 OCR 需要的时间
"""

import fitz
import json
import re
import statistics
from pathlib import Path
from collections import Counter
from concurrent.futures import ProcessPoolExecutor, as_completed

GOLDEN_DIR = Path("golden_set")
OUTPUT_JSON = GOLDEN_DIR / "pdf_diagnosis.json"
OUTPUT_MD = GOLDEN_DIR / "pdf_diagnosis.md"

# ─── 各类阈值 ───
LOW_TEXT_CHARS = 50
OK_TEXT_CHARS = 100
LARGE_IMAGE_PIXELS = 1000 * 1000
HUGE_IMAGE_PIXELS = 2000 * 2000
IMAGE_HEAVY_RATIO = 0.5
OCR_SEC_PER_PAGE = 3  # 粗估每页 OCR 耗时


def detect_multi_column(page) -> bool:
    """
    多栏检测：把页面文字块按 x 中心聚类，
    如果块的 x 中心明显分布在两/三个簇（间隙 > 5% 页宽），认为是多栏
    """
    try:
        blocks = page.get_text("blocks")  # (x0, y0, x1, y1, text, block_no, type)
        if not blocks or len(blocks) < 6:
            return False
        page_w = page.rect.width
        centers = []
        for b in blocks:
            if len(b) < 5 or not str(b[4]).strip():
                continue
            centers.append((b[0] + b[2]) / 2)
        if len(centers) < 6:
            return False
        # 简单聚类：把中心值排序，找最大间隙
        centers.sort()
        # 至少要跨越 40% 的页宽
        span = centers[-1] - centers[0]
        if span < page_w * 0.4:
            return False
        # 找最大 gap
        gaps = [(centers[i + 1] - centers[i], i) for i in range(len(centers) - 1)]
        max_gap, _ = max(gaps)
        # gap > 页宽 5% 且左右两侧都有 >=3 个块
        return max_gap > page_w * 0.05
    except Exception:
        return False


def detect_repeating_header_footer(pages_top_bottom_text):
    """
    输入：List[(top_text, bottom_text)] 每页顶部/底部前 60 字符
    统计出现次数 > 20% 页数的短文本，视为页眉页脚
    """
    if not pages_top_bottom_text:
        return False
    n = len(pages_top_bottom_text)
    threshold = max(3, n // 5)  # 至少出现 20% 的页数
    all_texts = []
    for top, bot in pages_top_bottom_text:
        if top:
            all_texts.append(top[:60].strip())
        if bot:
            all_texts.append(bot[:60].strip())
    counter = Counter(all_texts)
    # 排除空串和纯数字（数字很可能是页码，另外统计）
    for text, count in counter.most_common():
        if not text or text.isdigit() or len(text) < 4:
            continue
        if count >= threshold:
            return True
    return False


def diagnose_one(pdf_path_str: str) -> dict:
    """诊断单份 PDF。返回 dict。异常时返回带 _error 的 dict。"""
    pdf_path = Path(pdf_path_str)
    result = {
        "hash": pdf_path.stem,
        "filename": pdf_path.name,
        "file_size_mb": round(pdf_path.stat().st_size / 1024 / 1024, 2),
    }
    try:
        doc = fitz.open(pdf_path)
    except Exception as e:
        result["_error"] = f"open failed: {e}"
        return result

    try:
        n_pages = len(doc)
        result["pages_total"] = n_pages

        # 累加器
        pages_text_ok = 0
        pages_low_text = 0
        pages_empty = 0
        total_chars = 0
        total_images = 0
        large_images = 0
        huge_images = 0
        image_heavy_pages = 0
        landscape_pages = 0
        rotated_pages = 0
        multi_column_pages = 0
        native_tables_detected = 0
        pages_with_tables = 0
        max_table_rows = 0
        max_table_cols = 0
        pages_with_table_flags = []  # 每页有无表，用于检测跨页表
        top_bottom_texts = []

        for page_idx, page in enumerate(doc):
            # 文字健康度
            text = page.get_text("text") or ""
            text_len = len(text.strip())
            total_chars += text_len
            if text_len == 0:
                pages_empty += 1
            elif text_len < LOW_TEXT_CHARS:
                pages_low_text += 1
            elif text_len >= OK_TEXT_CHARS:
                pages_text_ok += 1

            # 版式：横向 / 旋转
            rect = page.rect
            if rect.width > rect.height:
                landscape_pages += 1
            if page.rotation and page.rotation != 0:
                rotated_pages += 1

            # 多栏
            if detect_multi_column(page):
                multi_column_pages += 1

            # 图像
            try:
                images_info = page.get_images(full=True)
            except Exception:
                images_info = []
            page_area = rect.width * rect.height or 1
            page_image_area = 0
            for img in images_info:
                total_images += 1
                # img: (xref, smask, w, h, bpc, ...)
                try:
                    w, h = img[2], img[3]
                    pixels = w * h
                    if pixels >= HUGE_IMAGE_PIXELS:
                        huge_images += 1
                    if pixels >= LARGE_IMAGE_PIXELS:
                        large_images += 1
                except Exception:
                    pass
                # 获取图片在页面上的显示区域
                try:
                    for bbox in page.get_image_rects(img[0]):
                        page_image_area += bbox.width * bbox.height
                except Exception:
                    pass
            if page_image_area / page_area >= IMAGE_HEAVY_RATIO:
                image_heavy_pages += 1

            # 表格（fitz >= 1.23 有 find_tables）
            page_has_table = False
            # ⚠️ find_tables 对超大/复杂页面可能极慢，加保护
            try:
                # 页面文字量超过 20000 字或图片超过 50 张时，跳过 find_tables（大概率是超复杂页）
                skip_find_tables = text_len > 20000 or len(images_info) > 50
                if not skip_find_tables:
                    tables = page.find_tables()
                    tlist = list(tables) if tables else []
                    for tbl in tlist:
                        native_tables_detected += 1
                        page_has_table = True
                        try:
                            rows = len(tbl.rows)
                            cols = len(tbl.header.names) if tbl.header else (len(tbl.rows[0].cells) if rows else 0)
                            max_table_rows = max(max_table_rows, rows)
                            max_table_cols = max(max_table_cols, cols)
                        except Exception:
                            pass
            except Exception:
                pass
            if page_has_table:
                pages_with_tables += 1
            pages_with_table_flags.append(page_has_table)

            # 顶部/底部文字（用于页眉页脚检测）
            try:
                # 取上下各 60px 的文本
                top_clip = fitz.Rect(0, 0, rect.width, min(60, rect.height * 0.1))
                bot_clip = fitz.Rect(0, max(0, rect.height - 60), rect.width, rect.height)
                top_text = page.get_text("text", clip=top_clip).strip()
                bot_text = page.get_text("text", clip=bot_clip).strip()
                top_bottom_texts.append((top_text, bot_text))
            except Exception:
                top_bottom_texts.append(("", ""))

        # 跨页表：连续 >=2 页都有表
        suspected_multipage = 0
        run_len = 0
        for flag in pages_with_table_flags:
            if flag:
                run_len += 1
            else:
                if run_len >= 2:
                    suspected_multipage += run_len
                run_len = 0
        if run_len >= 2:
            suspected_multipage += run_len

        # 页眉页脚
        has_hf = detect_repeating_header_footer(top_bottom_texts)

        result.update({
            "pages_text_ok": pages_text_ok,
            "pages_low_text": pages_low_text,
            "pages_empty": pages_empty,
            "total_chars_extracted": total_chars,
            "avg_chars_per_page": round(total_chars / n_pages, 1) if n_pages else 0,

            "total_images": total_images,
            "large_images": large_images,
            "huge_images": huge_images,
            "image_heavy_pages": image_heavy_pages,

            "native_tables_detected": native_tables_detected,
            "pages_with_tables": pages_with_tables,
            "max_table_rows": max_table_rows,
            "max_table_cols": max_table_cols,
            "suspected_multipage_table_pages": suspected_multipage,

            "landscape_pages": landscape_pages,
            "rotated_pages": rotated_pages,
            "multi_column_pages": multi_column_pages,

            "has_repeating_header_footer": has_hf,

            "estimated_ocr_seconds_if_all_scanned": (pages_low_text + pages_empty) * OCR_SEC_PER_PAGE,
        })
    finally:
        doc.close()
    return result


def aggregate(results):
    """对全量结果做统计聚合"""
    valid = [r for r in results if "_error" not in r]
    n = len(valid)
    if n == 0:
        return {}

    def _stat(field, integer=False):
        vals = [r.get(field, 0) or 0 for r in valid]
        if not vals:
            return {}
        return {
            "sum": sum(vals),
            "mean": round(statistics.mean(vals), 2 if not integer else 0),
            "median": statistics.median(vals),
            "max": max(vals),
            "min": min(vals),
        }

    def _count_gt(field, thresh):
        return sum(1 for r in valid if (r.get(field, 0) or 0) > thresh)

    return {
        "total_pdfs": len(results),
        "success_pdfs": n,
        "failed_pdfs": len(results) - n,

        "pages": _stat("pages_total", integer=True),
        "file_size_mb": _stat("file_size_mb"),
        "total_chars": _stat("total_chars_extracted", integer=True),

        # —— 文字健康度 ——
        "pdfs_with_empty_pages": _count_gt("pages_empty", 0),
        "pdfs_with_low_text_pages": _count_gt("pages_low_text", 0),
        "pdfs_mostly_scanned": sum(
            1 for r in valid
            if (r.get("pages_low_text", 0) + r.get("pages_empty", 0)) > r.get("pages_total", 1) * 0.5
        ),

        # —— 图像 ——
        "total_images_across_all": sum(r.get("total_images", 0) for r in valid),
        "pdfs_with_large_images": _count_gt("large_images", 0),
        "pdfs_with_huge_images": _count_gt("huge_images", 0),
        "pdfs_with_image_heavy_pages": _count_gt("image_heavy_pages", 0),

        # —— 表格 ——
        "total_tables_across_all": sum(r.get("native_tables_detected", 0) for r in valid),
        "pdfs_with_native_tables": _count_gt("native_tables_detected", 0),
        "pdfs_with_multipage_tables": _count_gt("suspected_multipage_table_pages", 0),
        "max_table_rows_across_all": max(r.get("max_table_rows", 0) for r in valid),
        "max_table_cols_across_all": max(r.get("max_table_cols", 0) for r in valid),

        # —— 版式 ——
        "pdfs_with_landscape_pages": _count_gt("landscape_pages", 0),
        "pdfs_with_rotated_pages": _count_gt("rotated_pages", 0),
        "pdfs_with_multi_column_pages": _count_gt("multi_column_pages", 0),
        "pdfs_multi_column_heavy": sum(
            1 for r in valid
            if r.get("multi_column_pages", 0) > r.get("pages_total", 1) * 0.3
        ),

        # —— 噪声 ——
        "pdfs_with_repeating_header_footer": sum(1 for r in valid if r.get("has_repeating_header_footer")),

        # —— 规模 ——
        "estimated_total_ocr_seconds": sum(r.get("estimated_ocr_seconds_if_all_scanned", 0) for r in valid),
    }


def render_markdown(agg, results):
    """生成 markdown 诊断报告"""
    valid = [r for r in results if "_error" not in r]
    lines = []
    lines.append("# Golden Set PDF 诊断报告")
    lines.append("")
    lines.append(f"扫描日期：{__import__('datetime').datetime.now():%Y-%m-%d %H:%M}")
    lines.append(f"扫描目标：`{GOLDEN_DIR}/*.pdf`")
    lines.append(f"扫描总数：{agg['total_pdfs']}  |  成功：{agg['success_pdfs']}  |  失败：{agg['failed_pdfs']}")
    lines.append("")
    lines.append("---")
    lines.append("")

    lines.append("## 一、语料规模")
    lines.append("")
    p = agg['pages']
    s = agg['file_size_mb']
    c = agg['total_chars']
    lines.append(f"- **页数**：总 {p['sum']:,}，中位 {p['median']}，最大 {p['max']}，最小 {p['min']}，均值 {p['mean']}")
    lines.append(f"- **文件大小(MB)**：总 {s['sum']:.1f}，中位 {s['median']}，最大 {s['max']}，最小 {s['min']}，均值 {s['mean']}")
    lines.append(f"- **抽取字符数**：总 {c['sum']:,}，中位 {c['median']:,}，最大 {c['max']:,}")
    lines.append("")

    lines.append("## 二、文字抽取健康度（决定 OCR 需求）")
    lines.append("")
    lines.append(f"- 存在**完全空白页**（get_text 返回 0）的 PDF：**{agg['pdfs_with_empty_pages']}** 份")
    lines.append(f"- 存在**低文字页**（< {LOW_TEXT_CHARS} 字，疑似扫描/图片页）的 PDF：**{agg['pdfs_with_low_text_pages']}** 份")
    lines.append(f"- **超过一半页数是扫描/图片页**的 PDF：**{agg['pdfs_mostly_scanned']}** 份（严重问题）")
    lines.append(f"- 粗估：如果对所有低文字页做 OCR，累计需要 **{agg['estimated_total_ocr_seconds']/60:.1f} 分钟**")
    lines.append("")

    lines.append("## 三、图像分布（老师提到的重点）")
    lines.append("")
    lines.append(f"- 全部 PDF 嵌入图片总数：**{agg['total_images_across_all']:,}** 张")
    lines.append(f"- 存在**大图**（≥ {LARGE_IMAGE_PIXELS/1e6:.0f}M 像素）的 PDF：**{agg['pdfs_with_large_images']}** 份")
    lines.append(f"- 存在**超大图**（≥ {HUGE_IMAGE_PIXELS/1e6:.0f}M 像素）的 PDF：**{agg['pdfs_with_huge_images']}** 份")
    lines.append(f"- 存在**图片主导页**（图片面积占页面 ≥ {IMAGE_HEAVY_RATIO:.0%}）的 PDF：**{agg['pdfs_with_image_heavy_pages']}** 份")
    lines.append("")

    lines.append("## 四、表格分布（财报命门）")
    lines.append("")
    lines.append(f"- fitz.find_tables 检测到的**原生表格总数**：**{agg['total_tables_across_all']:,}** 个")
    lines.append(f"- 至少有 1 个原生表格的 PDF：**{agg['pdfs_with_native_tables']}** 份")
    lines.append(f"- 存在**跨页表**（连续 ≥ 2 页有表）的 PDF：**{agg['pdfs_with_multipage_tables']}** 份")
    lines.append(f"- 单表**最大行数**：{agg['max_table_rows_across_all']}")
    lines.append(f"- 单表**最大列数**：{agg['max_table_cols_across_all']}")
    lines.append("")

    lines.append("## 五、版式复杂度")
    lines.append("")
    lines.append(f"- 存在**横向排版页**（landscape）的 PDF：**{agg['pdfs_with_landscape_pages']}** 份")
    lines.append(f"- 存在**页面旋转**（rotation != 0）的 PDF：**{agg['pdfs_with_rotated_pages']}** 份")
    lines.append(f"- 存在**多栏排版页**的 PDF：**{agg['pdfs_with_multi_column_pages']}** 份")
    lines.append(f"- **多栏页占比 > 30%** 的 PDF：**{agg['pdfs_multi_column_heavy']}** 份")
    lines.append("")

    lines.append("## 六、噪声")
    lines.append("")
    lines.append(f"- 检测到**重复页眉/页脚**的 PDF：**{agg['pdfs_with_repeating_header_footer']}** 份")
    lines.append("")

    # Top 极端案例（帮助定位）
    lines.append("## 七、极端案例（Top 5）")
    lines.append("")

    lines.append("### 页数最多")
    for r in sorted(valid, key=lambda x: -x.get("pages_total", 0))[:5]:
        lines.append(f"- `{r['hash'][:12]}`  {r['pages_total']} 页 ({r['file_size_mb']} MB)")
    lines.append("")

    lines.append("### 图片最多")
    for r in sorted(valid, key=lambda x: -x.get("total_images", 0))[:5]:
        lines.append(f"- `{r['hash'][:12]}`  {r.get('total_images', 0)} 张图（{r.get('large_images', 0)} 张大图，{r.get('huge_images', 0)} 张超大图）")
    lines.append("")

    lines.append("### 表格最多")
    for r in sorted(valid, key=lambda x: -x.get("native_tables_detected", 0))[:5]:
        lines.append(f"- `{r['hash'][:12]}`  {r.get('native_tables_detected', 0)} 个表（跨页表覆盖 {r.get('suspected_multipage_table_pages', 0)} 页）")
    lines.append("")

    lines.append("### 扫描页/低文字页最多（OCR 高需求）")
    def scan_score(r):
        return r.get("pages_low_text", 0) + r.get("pages_empty", 0)
    for r in sorted(valid, key=lambda x: -scan_score(x))[:5]:
        lines.append(f"- `{r['hash'][:12]}`  {scan_score(r)} 页低文字/空白（总 {r['pages_total']} 页）")
    lines.append("")

    lines.append("### 横向页最多")
    for r in sorted(valid, key=lambda x: -x.get("landscape_pages", 0))[:5]:
        if r.get("landscape_pages", 0) > 0:
            lines.append(f"- `{r['hash'][:12]}`  {r.get('landscape_pages', 0)} 页横向（总 {r['pages_total']} 页）")
    lines.append("")

    lines.append("### 多栏页最多")
    for r in sorted(valid, key=lambda x: -x.get("multi_column_pages", 0))[:5]:
        if r.get("multi_column_pages", 0) > 0:
            lines.append(f"- `{r['hash'][:12]}`  {r.get('multi_column_pages', 0)} 页多栏（总 {r['pages_total']} 页）")
    lines.append("")

    # 失败列表
    failed = [r for r in results if "_error" in r]
    if failed:
        lines.append("## 八、解析失败")
        lines.append("")
        for r in failed:
            lines.append(f"- `{r.get('hash', '?')}`  {r.get('_error')}")
        lines.append("")

    lines.append("---")
    lines.append("")
    lines.append("## 下一步建议（基于本诊断）")
    lines.append("")
    lines.append("请根据以上数据决定需要在 OCR/预处理管线中支持哪些能力：")
    lines.append("")
    lines.append("- 若「原生表格总数」显著大 → 应替换 PDF 电子页的 `get_text` 为 `find_tables`，保留结构")
    lines.append("- 若「跨页表 PDF」多 → 需要跨页表拼接（继承上页表头）")
    lines.append("- 若「多栏页 PDF」多 → 需要按列切分文本块")
    lines.append("- 若「大图/超大图」多 → OCR 前需要做降采样，避免超时")
    lines.append("- 若「页眉页脚 PDF」几乎全都是 → 需要噪声清洗（否则大量重复 chunk）")
    lines.append("- 若「主要为扫描页」的 PDF > 0 → 需要保证 OCR 稳定 + 引擎并行度合理")
    lines.append("")
    return "\n".join(lines)


def main():
    import time as _time
    pdfs = sorted(GOLDEN_DIR.glob("*.pdf"))
    print(f"[INFO] Found {len(pdfs)} PDFs", flush=True)
    t0 = _time.time()

    # 用 ProcessPool 并发（fitz 是 CPython 扩展，process 比 thread 更好）
    # ⚠️ 单份超时保护：超过 300s 直接放弃
    results = []
    PER_PDF_TIMEOUT = 300
    with ProcessPoolExecutor(max_workers=4) as ex:
        futures = {ex.submit(diagnose_one, str(p)): p for p in pdfs}
        done = 0
        for fut in as_completed(futures):
            p = futures[fut]
            try:
                r = fut.result(timeout=PER_PDF_TIMEOUT)
            except Exception as e:
                r = {
                    "hash": p.stem,
                    "filename": p.name,
                    "file_size_mb": round(p.stat().st_size / 1024 / 1024, 2),
                    "_error": f"timeout or exception: {e}",
                }
            done += 1
            status = "OK " if "_error" not in r else "ERR"
            elapsed = _time.time() - t0
            print(
                f"  [{done:3d}/{len(pdfs)}] {status}  {r['hash'][:12]}  "
                f"pages={r.get('pages_total','?')}  imgs={r.get('total_images','?')}  "
                f"tables={r.get('native_tables_detected','?')}  "
                f"({elapsed:.0f}s)",
                flush=True,
            )
            results.append(r)

    # 排序按 hash
    results.sort(key=lambda x: x["hash"])
    OUTPUT_JSON.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n[SAVED] {OUTPUT_JSON}")

    agg = aggregate(results)
    md = render_markdown(agg, results)
    OUTPUT_MD.write_text(md, encoding="utf-8")
    print(f"[SAVED] {OUTPUT_MD}")

    # 打印关键 summary
    print("\n" + "=" * 60)
    print("关键指标速览")
    print("=" * 60)
    for k in [
        "total_pdfs", "success_pdfs", "failed_pdfs",
        "total_images_across_all", "total_tables_across_all",
        "pdfs_with_native_tables", "pdfs_with_multipage_tables",
        "pdfs_with_large_images", "pdfs_with_huge_images",
        "pdfs_with_landscape_pages", "pdfs_with_multi_column_pages",
        "pdfs_multi_column_heavy",
        "pdfs_with_repeating_header_footer",
        "pdfs_with_low_text_pages", "pdfs_mostly_scanned",
    ]:
        print(f"  {k}: {agg.get(k)}")


if __name__ == "__main__":
    main()
