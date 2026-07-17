"""
批量提取 Golden Set 101 份 PDF 元信息 → metadata.json

策略：
1. 先用 PyMuPDF 提取每份 PDF 的首页 + 关键前几页文本（含标题页、目录页）
2. 用通义 qwen-plus 从文本中抽取结构化字段
3. 并发处理（受限于 API QPS，用小并发）
4. 失败自动重试；无法确定的字段填 null
"""
import os, json, re, asyncio, time
from pathlib import Path
import fitz
import dashscope
from dashscope import Generation

# ── 配置 ──
API_KEY = "sk-a44e988148db4ecbb158d1b1592fdc40"
GOLDEN_DIR = Path("golden_set")
OUTPUT = GOLDEN_DIR / "metadata.json"
MODEL = "qwen-plus"
CONCURRENCY = 4  # 并发数（qwen-plus 免费额度 QPS 有限）

dashscope.api_key = API_KEY

EXTRACT_PROMPT = """You are a financial document analyst. Extract structured metadata from the following annual report excerpts.

Return ONLY a JSON object (no markdown, no explanation) with these fields:
- company: Full company name (string, e.g. "Apple Inc.")
- ticker: Stock ticker symbol if visible (string or null, e.g. "AAPL")
- exchange: Stock exchange if identifiable (string or null, e.g. "NASDAQ", "NYSE", "ASX", "SSE", "HKEX")
- fiscal_year: Fiscal year the report covers (integer, e.g. 2022)
- report_type: One of ["10-K", "10-Q", "Annual Report", "Interim Report", "20-F", "Other"]
- industry: Primary industry (string, e.g. "Biotechnology", "Banking", "Automotive")
- country: Country of incorporation or primary listing (string, ISO country name)
- language: Primary language of the report ("en", "zh", or other ISO 639-1 code)
- currency: Reporting currency (string, e.g. "USD", "AUD", "CNY")

If a field cannot be determined from the excerpt, use null. Do NOT guess.

Excerpts:
---
{text}
---

Return the JSON object now:"""


def extract_pdf_text(pdf_path: Path, max_chars: int = 6000) -> str:
    """提取 PDF 前几页文本，重点抽标题页/封面/目录"""
    try:
        doc = fitz.open(pdf_path)
        # 前 3 页通常含标题/封面/目录
        pages_to_read = min(3, len(doc))
        parts = []
        for i in range(pages_to_read):
            text = doc[i].get_text()
            parts.append(f"[Page {i+1}]\n{text.strip()}")
        doc.close()
        combined = "\n\n".join(parts)
        return combined[:max_chars]
    except Exception as e:
        return f"[ERROR reading PDF: {e}]"


def call_llm(text: str, retries: int = 3) -> dict:
    """调用通义抽取元信息，带重试"""
    prompt = EXTRACT_PROMPT.format(text=text)
    last_err = None
    for attempt in range(retries):
        try:
            resp = Generation.call(
                model=MODEL,
                prompt=prompt,
                result_format="message",
                temperature=0.1,
                max_tokens=500,
            )
            if resp.status_code != 200:
                last_err = f"HTTP {resp.status_code}: {resp.message}"
                time.sleep(1 + attempt)
                continue
            content = resp.output.choices[0].message.content.strip()
            # 提取 JSON（可能带 markdown code fence）
            m = re.search(r"\{[\s\S]*\}", content)
            if not m:
                last_err = f"No JSON found in: {content[:200]}"
                continue
            data = json.loads(m.group(0))
            return data
        except Exception as e:
            last_err = str(e)
            time.sleep(1 + attempt)
    return {"_error": last_err}


def process_one(pdf_path: Path) -> dict:
    """处理单份 PDF"""
    hash_id = pdf_path.stem
    file_size = pdf_path.stat().st_size
    try:
        doc = fitz.open(pdf_path)
        page_count = len(doc)
        doc.close()
    except Exception:
        page_count = None

    text = extract_pdf_text(pdf_path)
    meta = call_llm(text)

    return {
        "hash": hash_id,
        "filename": pdf_path.name,
        "file_size_kb": round(file_size / 1024),
        "page_count": page_count,
        **meta,
    }


async def main():
    pdfs = sorted(GOLDEN_DIR.glob("*.pdf"))
    print(f"[INFO] Found {len(pdfs)} PDF files")

    sem = asyncio.Semaphore(CONCURRENCY)
    results = []

    async def bound(pdf):
        async with sem:
            # 在 executor 里跑同步代码
            loop = asyncio.get_event_loop()
            return await loop.run_in_executor(None, process_one, pdf)

    tasks = [bound(p) for p in pdfs]
    done = 0
    for coro in asyncio.as_completed(tasks):
        r = await coro
        done += 1
        status = "OK" if "_error" not in r else "ERR"
        company = r.get("company", "?")
        year = r.get("fiscal_year", "?")
        print(f"  [{done:3d}/{len(pdfs)}] {status}  {r['hash'][:12]}  {company}  {year}")
        results.append(r)

    # 按 hash 排序
    results.sort(key=lambda x: x["hash"])
    OUTPUT.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n[DONE] Written {len(results)} records to {OUTPUT}")

    # 统计
    ok = sum(1 for r in results if "_error" not in r)
    print(f"[STATS] Success: {ok}/{len(results)}")


if __name__ == "__main__":
    asyncio.run(main())
