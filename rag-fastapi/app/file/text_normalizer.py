"""财报文本归一化（检索层用）

第一性原理
----------
1. 原文入库不改，只在检索时对 query 和 doc chunk 双端做归一化。
   → 命中率提升，同时保留原文供答案返回。
2. 归一化用保守正则，宁少改不多改。只处理明确格式；模糊格式不动。
3. 5 类 normalizer 独立函数，可单独测；对外提供 normalize_for_retrieval() 总入口。
4. annotate() 返回文本里包含哪些 flag（"含负数"、"含 FY"、"含单位"），
   供 chunk metadata 用于粗排过滤。

覆盖范围（依据 research/content_frequency.json 真实数据）
------------------------------------------------------
- 千分位数字     密度 686.7/100 页
- 4 位年份       密度 656.6/100 页
- 括号负数       密度 218.1/100 页
- 英文日期       密度 179.0/100 页
- 单位 million/billion/亿/万  密度 160.9/100 页
- FY22 / fiscal 2022  密度 47.1/100 页

不覆盖（明确剔除）
----------------
- ISO 日期 2022-12-31（样本 0 命中）
- 币种代码 USD/EUR（样本 1.1，交给 Prompt）
- 币种符号 $€£¥（保留原文，不归一化以免破坏 chunk 可读性）
- 财务缩写 EBITDA（LLM 天然认识，交给 Prompt）
"""

from __future__ import annotations

import re
from typing import Dict, List

# ---------------------------------------------------------------------------
# 1. 千分位数字：1,234,567 → 1234567
# ---------------------------------------------------------------------------
# 规则：至少 1 个逗号，两侧必须是数字，逗号后恰好 3 位数字
# 反例避免：日期 "Dec 31, 2022" 中的逗号（后面是空格），列表逗号
_THOUSANDS_RE = re.compile(r"(?<=\d),(?=\d{3}(?!\d))")


def normalize_thousands(text: str) -> str:
    """去掉千分位逗号。1,234,567 → 1234567"""
    return _THOUSANDS_RE.sub("", text)


# ---------------------------------------------------------------------------
# 2. 括号负数：(1,234) 或 (1234.5) → -1234
# ---------------------------------------------------------------------------
# 第一性原理：财报里括号负数一定是"财务数据"，通常伴随千分位或小数点或大数字。
# 反例：Note (3)、item (2)、section (a)(1) 都是脚注引用，不是负数。
# 规则：括号内必须满足以下之一才判为负数：
#   (a) 含千分位逗号：(1,234)
#   (b) 含小数点：(12.5)
#   (c) 纯整数且 ≥ 4 位：(9999)
# 这样 (3) / (12) / (99) 全部保留原文（视作脚注引用）
_PAREN_NEG_RE = re.compile(
    r"\("
    r"("
    r"\d{1,3}(?:,\d{3})+(?:\.\d+)?"  # (a) 千分位
    r"|"
    r"\d+\.\d+"                       # (b) 小数
    r"|"
    r"\d{4,}"                         # (c) ≥ 4 位整数
    r")"
    r"\)"
)


def normalize_paren_negative(text: str) -> str:
    """财报括号负数转负号。(1,234) → -1234；(3) 保留原文"""
    def _repl(m: re.Match) -> str:
        num = m.group(1).replace(",", "")
        return f"-{num}"
    return _PAREN_NEG_RE.sub(_repl, text)


# ---------------------------------------------------------------------------
# 3. 单位归一化：million/billion/亿/万 → 统一后缀
# ---------------------------------------------------------------------------
# 财报常见搭配：$99.5 million / EUR 1.2 billion / 99 亿 / 5万
# 归一化目标：数字 + 空格 + 单位小写全称
# 这样 "$99M" 和 "$99 million" 都会归一到 "99 million"，供 embedding 认

_UNIT_MAP = {
    "m": "million",
    "mm": "million",  # 美式财报常用
    "mn": "million",
    "million": "million",
    "b": "billion",
    "bn": "billion",
    "billion": "billion",
    "k": "thousand",
    "thousand": "thousand",
    "亿": "亿",
    "万": "万",
}

# 匹配 "数字 [空格] 单位"
# 数字：可含千分位、小数点、正负号
# 单位：上表 key 之一，前后需为词边界（避免 "modem" 命中 m）
_UNIT_RE = re.compile(
    r"(-?\d+(?:,\d{3})*(?:\.\d+)?)\s*"
    r"(million|billion|thousand|mm|mn|bn|亿|万|[MBKmbk])"
    r"(?=[\s.,;:!?)\]}]|$)",  # 后跟空白/标点/结尾
)


def normalize_units(text: str) -> str:
    """单位归一化。$99M → 99 million；EUR 1.2bn → 1.2 billion"""
    def _repl(m: re.Match) -> str:
        num = m.group(1).replace(",", "")
        unit_raw = m.group(2).lower()
        unit_full = _UNIT_MAP.get(unit_raw, unit_raw)
        return f"{num} {unit_full}"
    return _UNIT_RE.sub(_repl, text)


# ---------------------------------------------------------------------------
# 4. 年份归一化：FY22 / fiscal 2022 / FY2022 → 2022
# ---------------------------------------------------------------------------
# 规则：
#   - FY + 2 位数字 (20-29) → 20xx
#   - FY + 4 位数字 → 保留 4 位
#   - fiscal year ended <date>  → 保留原文（信息量在日期里）
#   - fiscal 2022 → 2022

_FY_2DIGIT_RE = re.compile(r"\bFY\s*['\u2019]?(\d{2})\b", re.IGNORECASE)
_FY_4DIGIT_RE = re.compile(r"\bFY\s*(\d{4})\b", re.IGNORECASE)
_FISCAL_YEAR_RE = re.compile(r"\bfiscal\s+(?:year\s+)?(\d{4})\b", re.IGNORECASE)


def normalize_fiscal_year(text: str) -> str:
    """财年归一化。FY22 → FY2022；fiscal 2022 → FY2022"""
    # 先处理 4 位：FY2022 → FY2022（大小写归一）
    text = _FY_4DIGIT_RE.sub(lambda m: f"FY{m.group(1)}", text)
    # 再处理 2 位：FY22 → FY2022（20/21/22/23/24/25/26 → 20xx；其它保守不动）
    def _fy2(m: re.Match) -> str:
        yy = int(m.group(1))
        if 15 <= yy <= 35:   # 保守范围，覆盖财报常见年份
            return f"FY20{yy:02d}"
        return m.group(0)
    text = _FY_2DIGIT_RE.sub(_fy2, text)
    # fiscal 2022 → FY2022
    text = _FISCAL_YEAR_RE.sub(lambda m: f"FY{m.group(1)}", text)
    return text


# ---------------------------------------------------------------------------
# 5. 英文日期归一化：Dec 31, 2022 → 2022-12-31
# ---------------------------------------------------------------------------
_MONTHS = {
    "jan": "01", "january": "01",
    "feb": "02", "february": "02",
    "mar": "03", "march": "03",
    "apr": "04", "april": "04",
    "may": "05",
    "jun": "06", "june": "06",
    "jul": "07", "july": "07",
    "aug": "08", "august": "08",
    "sep": "09", "sept": "09", "september": "09",
    "oct": "10", "october": "10",
    "nov": "11", "november": "11",
    "dec": "12", "december": "12",
}

# 格式 1: Dec 31, 2022  /  December 31, 2022
_DATE_MDY_RE = re.compile(
    r"\b(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec|"
    r"January|February|March|April|June|July|August|September|October|November|December)"
    r"\s+(\d{1,2}),?\s+(\d{4})\b",
    re.IGNORECASE,
)
# 格式 2: 31 December 2022 (欧式)
_DATE_DMY_RE = re.compile(
    r"\b(\d{1,2})\s+"
    r"(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec|"
    r"January|February|March|April|June|July|August|September|October|November|December)"
    r"\s+(\d{4})\b",
    re.IGNORECASE,
)


def normalize_dates(text: str) -> str:
    """英文日期归一化到 ISO。Dec 31, 2022 → 2022-12-31"""
    def _mdy(m: re.Match) -> str:
        mon = _MONTHS[m.group(1).lower()]
        day = int(m.group(2))
        year = m.group(3)
        return f"{year}-{mon}-{day:02d}"
    def _dmy(m: re.Match) -> str:
        day = int(m.group(1))
        mon = _MONTHS[m.group(2).lower()]
        year = m.group(3)
        return f"{year}-{mon}-{day:02d}"
    text = _DATE_MDY_RE.sub(_mdy, text)
    text = _DATE_DMY_RE.sub(_dmy, text)
    return text


# ---------------------------------------------------------------------------
# 总入口
# ---------------------------------------------------------------------------
def normalize_for_retrieval(text: str) -> str:
    """检索层归一化总入口。query 和 doc chunk 都过这一层。

    顺序：日期 → 财年 → 括号负数 → 千分位 → 单位
    （日期在最前，避免逗号被千分位规则误吃）
    """
    if not text:
        return text
    text = normalize_dates(text)
    text = normalize_fiscal_year(text)
    text = normalize_paren_negative(text)
    text = normalize_thousands(text)
    text = normalize_units(text)
    return text


# ---------------------------------------------------------------------------
# 附加：为 chunk 打 metadata flag
# ---------------------------------------------------------------------------
def annotate(text: str) -> Dict[str, bool]:
    """返回 chunk 里包含哪些语义 flag，供 metadata 粗排用"""
    return {
        "has_negative": bool(_PAREN_NEG_RE.search(text)),
        "has_unit": bool(_UNIT_RE.search(text)),
        "has_fiscal_year": bool(
            _FY_2DIGIT_RE.search(text)
            or _FY_4DIGIT_RE.search(text)
            or _FISCAL_YEAR_RE.search(text)
        ),
        "has_date": bool(_DATE_MDY_RE.search(text) or _DATE_DMY_RE.search(text)),
        "has_thousands": bool(_THOUSANDS_RE.search(text)),
    }


# ---------------------------------------------------------------------------
# 自测
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    cases = [
        # (输入, 期望)
        ("Revenue was $1,234,567 in 2022.", "Revenue was $1234567 in 2022."),
        ("Loss of (1,234) million", "Loss of -1234 million"),
        ("EBITDA of $99.5M grew 5%.", "EBITDA of $99.5 million grew 5%."),
        ("FY22 revenue was EUR 1.2bn", "FY2022 revenue was EUR 1.2 billion"),
        ("Fiscal 2022 results", "FY2022 results"),
        ("As of Dec 31, 2022", "As of 2022-12-31"),
        ("Ended 31 December 2022", "Ended 2022-12-31"),
        ("See Note (3) for details", "See Note (3) for details"),  # 括号不该转
        ("Modem sales grew", "Modem sales grew"),  # 不该被单位吃
        ("Impairment (12.5) million", "Impairment -12.5 million"),  # 小数负数
        ("Cash (9999)", "Cash -9999"),                              # 4 位整数负数
        ("Item (a)(1) below", "Item (a)(1) below"),                 # 脚注引用保留
    ]
    print("=" * 60)
    print("text_normalizer 自测")
    print("=" * 60)
    passed = 0
    for src, expected in cases:
        got = normalize_for_retrieval(src)
        ok = got == expected
        passed += ok
        mark = "OK" if ok else "FAIL"
        print(f"[{mark}] {src!r}")
        if not ok:
            print(f"      expected: {expected!r}")
            print(f"      got:      {got!r}")
    print("-" * 60)
    print(f"{passed}/{len(cases)} passed")

    print("\nannotate 示例:")
    demo = "FY22 revenue was $1,234M, loss (500). As of Dec 31, 2022."
    print(f"  {demo!r}")
    print(f"  {annotate(demo)}")
