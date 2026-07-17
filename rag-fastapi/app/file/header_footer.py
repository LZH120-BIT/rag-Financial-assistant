"""
页眉页脚清洗器
==============

两阶段：
1. **学习阶段**：扫描全文档，统计"顶部/底部区域的重复文本"，构建模板集合
2. **清洗阶段**：对每页 blocks 过滤掉命中模板的 block

**通用规则**（几何 + 频率，不依赖内容语义/语言）：
- 只统计位于页顶 10% 或页底 10% 的 text block
- 文本先做"数字归一化"（页码剥离）：`10 · Company Report 2022` -> `# · Company Report 2022`
- 出现在 ≥ 30% 页的归一化文本 -> 加入模板
- 过滤时按归一化后是否命中模板判断

**边界情况**：
- 短文档（<10 页）：跳过学习阶段（样本太少统计不可靠）
- 首页/末页封面：模板通常也不会污染这两页
"""

from __future__ import annotations

import re
from collections import Counter

import fitz

# 页顶/页底区域比例
HEADER_ZONE = 0.10
FOOTER_ZONE = 0.90  # y0 > page_h * 0.90 视为页脚
# 判定为模板的最小页面覆盖率
TEMPLATE_MIN_COVERAGE = 0.30
# 学习阶段最少页数
MIN_PAGES_FOR_LEARNING = 10


_NUM_RE = re.compile(r"\d+")
_WS_RE = re.compile(r"\s+")


def normalize_hf_text(text: str) -> str:
    """归一化：把所有数字段替换为 #，压缩空白。
    这样页码变化 (10 -> 11 -> 12) 不影响频率统计。
    """
    if not text:
        return ""
    s = _NUM_RE.sub("#", text)
    s = _WS_RE.sub(" ", s).strip()
    return s


# 保留旧名字（内部用）
_normalize = normalize_hf_text


def learn_templates(doc: fitz.Document) -> set[str]:
    """扫描全文档，返回归一化后的模板文本集合。"""
    n_pages = doc.page_count
    if n_pages < MIN_PAGES_FOR_LEARNING:
        return set()

    freq: Counter = Counter()
    for pno in range(n_pages):
        page = doc[pno]
        page_h = page.rect.height
        if page_h <= 0:
            continue
        seen_on_page: set[str] = set()
        for b in page.get_text("blocks"):
            if len(b) < 7 or b[6] != 0:
                continue
            text = (b[4] or "").strip()
            if not text:
                continue
            y0, y1 = b[1], b[3]
            in_header = y1 < page_h * HEADER_ZONE
            in_footer = y0 > page_h * FOOTER_ZONE
            if not (in_header or in_footer):
                continue
            norm = _normalize(text)
            if not norm or len(norm) < 3:
                continue
            # 每页每个 norm 只计一次（避免重复 block 灌水）
            if norm in seen_on_page:
                continue
            seen_on_page.add(norm)
            freq[norm] += 1

    threshold = max(3, int(n_pages * TEMPLATE_MIN_COVERAGE))
    templates = {norm for norm, cnt in freq.items() if cnt >= threshold}
    return templates


def strip_header_footer_from_blocks(
    blocks: list[tuple],
    page_height: float,
    templates: set[str],
) -> list[tuple]:
    """从一页的 blocks 里过滤掉命中模板的 header/footer block。
    Args:
        blocks: page.get_text("blocks") 的输出（原生元组格式）
        page_height: 页面高度
        templates: learn_templates 返回的模板集合
    Returns:
        过滤后的 blocks 列表（原始 tuple 结构）
    """
    if not templates or page_height <= 0:
        return list(blocks)
    out = []
    for b in blocks:
        if len(b) < 7 or b[6] != 0:
            out.append(b)
            continue
        text = (b[4] or "").strip()
        y0, y1 = b[1], b[3]
        in_header = y1 < page_height * HEADER_ZONE
        in_footer = y0 > page_height * FOOTER_ZONE
        if (in_header or in_footer) and text:
            if _normalize(text) in templates:
                continue  # 丢弃
        out.append(b)
    return out


def strip_header_footer_texts(
    page: fitz.Page,
    templates: set[str],
) -> str:
    """便捷函数：对一页 get_text("text") 结果按行过滤命中模板的行。
    只在没有 blocks 处理时兜底用；主流程建议用 strip_header_footer_from_blocks。
    """
    if not templates:
        return page.get_text("text")
    lines = page.get_text("text").splitlines()
    kept = [ln for ln in lines if _normalize(ln.strip()) not in templates]
    return "\n".join(kept)


# ---------- 自测 ----------

if __name__ == "__main__":
    import sys
    from pathlib import Path
    SAMPLES = [
        ("SIMPLE",       "30f64d1043f4"),
        ("MULTIPAGE",    "ded965ce7e3e"),
        ("MULTI_COLUMN", "696ddc4c80fe"),
        ("IMAGE_HEAVY",  "78c71282723c"),
        ("LANDSCAPE",    "cc0fc5888b99"),
    ]
    for tag, prefix in SAMPLES:
        pdf = next(Path("golden_set").glob(f"{prefix}*.pdf"))
        doc = fitz.open(pdf)
        templates = learn_templates(doc)
        print(f"\n[{tag:14s}] {doc.page_count}p -> {len(templates)} templates learned:")
        for t in list(templates)[:6]:
            print(f"    {t[:80]!r}")
        doc.close()
