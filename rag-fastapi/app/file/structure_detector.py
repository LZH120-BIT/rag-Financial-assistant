"""财报文档结构检测

目标
----
1. 目录页识别 (TOC)：识别后打 `section=toc` 标签，供检索降权
2. 五大分区打标：MD&A / Risk / Audit / Appendix / Financial Statements
   每页判断"最近一次分区标题触发"，chunk 打对应 section 标签

不做的事（保守策略，等 badcase 迭代）
------------------------------------
- 章节层级树（doc.get_toc() 只 10% 可用 + 字号推断不稳，成本大于收益）
- 侧边栏 / 页边注（P2 KL）
- 上标脚注（P2 KL）

第一性原理
----------
- 目录页 pattern：一页里出现 ≥5 个 "…字…数字" 结构（文字 + 页码），
  且整页字数少（<3000 字，因为目录页文字稀）
- 分区标题 pattern：整行是分区名（case-insensitive，独占一行 or 前后 <20 字），
  遇到就把该页起后续所有页标为该 section，直到遇到下一个分区
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional


# ---------------------------------------------------------------------------
# 目录页识别
# ---------------------------------------------------------------------------

# TOC 行 pattern: 文本 + 空白/dots + 页码结尾
# 例：Introduction ........... 5
# 例：Item 1. Business    12
# 例：Risk Factors\t\t\t17
_TOC_LINE_RE = re.compile(
    r"^\s*"
    r"(?=.{3,80}\d\s*$)"                        # 长度 3-80，行末必须是数字
    r"[\wA-Za-z][\w\s,'&./()\-]{2,70}?"          # 文字部分
    r"[\s.\-]{2,}"                               # 至少两个 dots/spaces/dashes
    r"\d{1,4}\s*$"
)

# 目录关键词（首行判断）
_TOC_KEYWORDS_RE = re.compile(
    r"^\s*(table\s+of\s+contents?|index(\s+to\s+.+)?|contents?|目\s*录)\s*$",
    re.IGNORECASE | re.MULTILINE,
)

# 孤行页码：一行只有 1-3 位数字（TOC 拆行常见；1000+ 是财务金额，排除）
_PAGE_NUMBER_LINE_RE = re.compile(r"^\s*\d{1,3}\s*$", re.MULTILINE)


def is_toc_page(page_text: str) -> bool:
    """判断该页是否目录页。规则（任一即真）：
      A. 全页 <3000 字 且 首行含 "Table of Contents" / "INDEX" / "Contents" / "目录"
      B. 全页 <3000 字 且 ≥5 个"内联 TOC 行" (文字+dots+页码)
      C. 全页 <2000 字 且 孤行页码占非空行 ≥30% (SEC 10-K 目录拆行常见)
    """
    if not page_text:
        return False
    if len(page_text) > 3000:
        return False
    # A: 关键词
    if _TOC_KEYWORDS_RE.search(page_text[:500]):
        return True
    # B: 内联 TOC 行
    lines = page_text.splitlines()
    if sum(1 for ln in lines if _TOC_LINE_RE.match(ln)) >= 5:
        return True
    # C: 孤行页码（严：<2000 字 且 占比 >=30%）
    if len(page_text) < 2000:
        non_empty = [ln for ln in lines if ln.strip()]
        if non_empty:
            page_num_lines = sum(1 for ln in non_empty if _PAGE_NUMBER_LINE_RE.match(ln))
            if page_num_lines / len(non_empty) >= 0.30 and page_num_lines >= 5:
                return True
    return False


# ---------------------------------------------------------------------------
# 五大分区关键词（英文财报为主，中文备用）
# ---------------------------------------------------------------------------

SECTION_PATTERNS = {
    "mda": [
        r"^\s*(item\s+\d+[a-z]?[.:]?\s*)?management['\u2019]s?\s+discussion\s+and\s+analysis\b",
        r"^\s*MD\s*&\s*A\b",
        r"^\s*operating\s+and\s+financial\s+review\b",
        r"^\s*business\s+review\b",
        r"^\s*经营\s*讨论\s*(与|和)?\s*分析",
    ],
    "risk": [
        r"^\s*(item\s+\d+[a-z]?[.:]?\s*)?risk\s+factors\b",
        r"^\s*principal\s+risks(\s+and\s+uncertainties)?\b",
        r"^\s*风险\s*因素",
        r"^\s*主要\s*风险",
    ],
    "audit": [
        r"^\s*report\s+of\s+independent\s+(registered\s+public\s+)?accounting\s+firm\b",
        r"^\s*independent\s+(registered\s+public\s+)?accounting\s+firm['\u2019]?s?\s+report\b",
        r"^\s*independent\s+auditor['\u2019]?s?\s+report\b",
        r"^\s*独立\s*(注册\s*)?会计师\s*报告",
        r"^\s*审计\s*报告\b",
    ],
    "appendix": [
        r"^\s*appendi(x|ces)\b",
        r"^\s*exhibits?\b",
        r"^\s*supplement(al|ary)\s+(information|data|financial)",
        r"^\s*附录",
    ],
    "financials": [
        r"^\s*consolidated\s+(balance\s+sheets?|statements?\s+of\s+\w+|financial\s+statements?)\b",
        r"^\s*(item\s+\d+[a-z]?[.:]?\s*)?financial\s+statements?\s+and\s+supplementary\s+data",
        r"^\s*notes?\s+to\s+(consolidated\s+)?financial\s+statements",
        r"^\s*合并\s*(资产负债表|利润表|现金流量表)",
        r"^\s*财务\s*报表",
    ],
}

_SECTION_REGEXES = {
    name: [re.compile(p, re.IGNORECASE) for p in pats]
    for name, pats in SECTION_PATTERNS.items()
}


def detect_section_title(text: str) -> Optional[str]:
    """如果 text 里存在某分区的标题级出现（独占一行 or 短文本行首），返回 section 名。
    规则：
      1. 遍历每一行，长度 <=140 的行（放宽到 140：SEC 10-K 的 MD&A 完整标题 "Item 7. Management's Discussion
         and Analysis of Financial Condition and Results of Operations: (continued)" 长度 108 字符；
         财报正文段落一般 >150 字，仍能过滤掉正文）
      2. 命中任一分区 regex 即返回该 section
      3. 优先级：audit > mda > risk > appendix > financials
         （mda 优于 risk：MD&A 里常出现 "risks and uncertainties" 措辞，如果 risk 优先会误判；
          MD&A 标题 "Item 7. Management's Discussion" 独一无二，risk 标题 "Item 1A. Risk Factors" 独一无二，
          两者互斥无冲突，但正文行文里 mda 页更容易带 risk 关键词，故 mda 优先。）
    """
    if not text:
        return None
    order = ["audit", "mda", "risk", "appendix", "financials"]
    for ln in text.splitlines():
        ln_s = ln.strip()
        if not ln_s or len(ln_s) > 140:
            continue
        for name in order:
            for rx in _SECTION_REGEXES[name]:
                if rx.search(ln_s):
                    return name
    return None


# ---------------------------------------------------------------------------
# 页级 section 归属计算
# ---------------------------------------------------------------------------


@dataclass
class PageStructureInfo:
    """单页结构信息"""
    page_no: int          # 0-based
    is_toc: bool = False
    section: Optional[str] = None    # mda/risk/audit/appendix/financials/None


def compute_page_sections(page_texts: list[str]) -> list[PageStructureInfo]:
    """给整文档所有页文本，计算每页的 (is_toc, section)。
    section 传播规则：遇到新分区标题就切换；否则继承前一页 section。
    """
    infos: list[PageStructureInfo] = []
    current_section: Optional[str] = None
    for pno, text in enumerate(page_texts):
        toc = is_toc_page(text)
        # 目录页不切换 section（目录里也会命中"Risk Factors"关键词）
        if toc:
            infos.append(PageStructureInfo(pno, is_toc=True, section="toc"))
            continue
        # 分区标题检测
        detected = detect_section_title(text)
        if detected:
            current_section = detected
        infos.append(PageStructureInfo(pno, is_toc=False, section=current_section))
    return infos


# ---------------------------------------------------------------------------
# 自测
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    # 目录行 pattern
    toc_cases = [
        ("Introduction ........... 5", True),
        ("Item 1. Business                12", True),
        ("Risk Factors\t\t17", True),
        ("Revenue was $1,234 in 2022.", False),
        ("A", False),
        ("2022", False),
    ]
    print("TOC line pattern:")
    for line, exp in toc_cases:
        got = bool(_TOC_LINE_RE.match(line))
        mark = "OK" if got == exp else "FAIL"
        print(f"  [{mark}] {line!r} -> {got}")

    # section 检测
    sec_cases = [
        ("Item 1A. Risk Factors", "risk"),
        ("Management's Discussion and Analysis of Financial Condition", "mda"),
        ("Report of Independent Registered Public Accounting Firm", "audit"),
        ("Consolidated Balance Sheets", "financials"),
        ("Appendix A: Additional Information", "appendix"),
        ("独立会计师报告", "audit"),
        ("Random paragraph about weather", None),
    ]
    print("\nSection detection:")
    for text, exp in sec_cases:
        got = detect_section_title(text)
        mark = "OK" if got == exp else "FAIL"
        print(f"  [{mark}] {text!r} -> {got} (exp {exp})")

    # 真实 PDF：显示前 100 页 section 分布
    import sys, pymupdf
    if len(sys.argv) > 1:
        doc = pymupdf.open(sys.argv[1])
        texts = [doc[i].get_text() for i in range(min(100, doc.page_count))]
        infos = compute_page_sections(texts)
        print(f"\n{sys.argv[1]} 前 {len(infos)} 页分区分布:")
        from collections import Counter
        cnt = Counter((i.section or "none") for i in infos)
        print(f"  {dict(cnt)}")
        toc_pages = [i.page_no + 1 for i in infos if i.is_toc]
        print(f"  目录页: {toc_pages}")
        doc.close()
