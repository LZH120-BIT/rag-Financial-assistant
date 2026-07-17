"""补齐 company 缺失的记录：读取更多页 + 用不同 prompt 重试"""
import json, re, time
from pathlib import Path
import fitz
import dashscope
from dashscope import Generation

dashscope.api_key = "sk-a44e988148db4ecbb158d1b1592fdc40"

META_FILE = Path("golden_set/metadata.json")
data = json.load(open(META_FILE))
missing = [r for r in data if not r.get("company")]
print(f"Retry {len(missing)} records with company=null\n")

PROMPT = """Extract metadata from these annual report pages. Return ONLY JSON, no explanation.

Fields (use null if truly not found):
- company (full name)
- ticker
- exchange
- fiscal_year (int)
- report_type (10-K/10-Q/Annual Report/20-F/Other)
- industry
- country
- language (ISO code)
- currency

Text:
---
{text}
---
JSON:"""


def extract_more(pdf_path: Path, max_chars: int = 12000) -> str:
    """读前 8 页 + 找可能含公司名的页"""
    doc = fitz.open(pdf_path)
    n = len(doc)
    # 前 8 页 + 靠后的目录页
    pages = list(range(min(8, n)))
    parts = []
    for i in pages:
        text = doc[i].get_text().strip()
        if text:
            parts.append(f"[Page {i+1}]\n{text}")
    doc.close()
    return "\n\n".join(parts)[:max_chars]


def call(text: str, retries=3):
    for a in range(retries):
        try:
            r = Generation.call(
                model="qwen-plus",
                prompt=PROMPT.format(text=text),
                result_format="message",
                temperature=0.1,
                max_tokens=600,
            )
            if r.status_code != 200:
                time.sleep(1 + a); continue
            c = r.output.choices[0].message.content
            m = re.search(r"\{[\s\S]*\}", c)
            if m: return json.loads(m.group(0))
        except Exception as e:
            time.sleep(1 + a)
    return None


updated = 0
for rec in missing:
    p = Path("golden_set") / rec["filename"]
    text = extract_more(p)
    new_meta = call(text)
    if new_meta and new_meta.get("company"):
        # 合并新字段（只更新原为 null 的）
        for k, v in new_meta.items():
            if v is not None and not rec.get(k):
                rec[k] = v
        print(f"  ✓ {rec['hash'][:12]}  → {rec.get('company')} ({rec.get('country')})")
        updated += 1
    else:
        print(f"  ✗ {rec['hash'][:12]}  仍无法识别（{rec['page_count']} 页）")

# 保存
META_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
print(f"\n补齐 {updated}/{len(missing)} 份，已更新 {META_FILE}")
