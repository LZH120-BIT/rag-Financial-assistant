"""
Golden QA v0 评测脚本（20 题初步评估）

评测 4 项指标：
  1. Context Recall  — 召回的 chunk 是否覆盖 ground_truth_pages
  2. Section Hit Rate — 命中 chunk 是否落在 expected_section
  3. 数字精确率      — LLM 答案里是否含 expected_key_facts_regex
  4. 拒答正确率      — should_refuse=true 的题是否真的拒答
  5. 延迟           — 检索 + LLM 生成端到端时间

用法:
  python3 scripts/run_eval_v0.py            # 跑全部 20 题
  python3 scripts/run_eval_v0.py --only Q01,Q02
  python3 scripts/run_eval_v0.py --no-llm   # 只跑检索评测（不调 LLM，快）
"""
from __future__ import annotations
import sys, os, asyncio, json, re, time, argparse
from pathlib import Path
from typing import Any, Optional

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "rag-fastapi"))
os.chdir(ROOT / "rag-fastapi")

from pymilvus import connections
from app.config import settings
from app.file.service import file_service
from app.chat.service import chat_service  # 用于生成答案


QA_PATH = ROOT / "evaluation" / "golden_qa_v0.jsonl"
REPORT_PATH = ROOT / "evaluation" / "report_v0.md"
RAW_OUT_PATH = ROOT / "evaluation" / "run_v0_raw.jsonl"

TOP_K = 5


def connect_milvus():
    h, p = settings.MILVUS_ADDRESS.split(":")
    connections.connect(alias="default", host=h, port=p)


def load_qa():
    qas = []
    for line in open(QA_PATH, encoding="utf-8"):
        line = line.strip()
        if line:
            qas.append(json.loads(line))
    return qas


def parse_chunks_from_search(res: dict) -> list[dict]:
    """从 search_shared_database 返回的 searchDocText 里解析出 chunks。
    chunk 头部形如 `1.[Company 2022 section p.NN]body...`
    """
    body = res["searchDocText"].split("文档内容:", 1)[-1]
    if "&没有检索到相关文档&" in body:
        return []
    parts = re.split(r"(?=^\d+\.\[)", body, flags=re.MULTILINE)
    chunks = []
    for p in parts:
        p = p.strip()
        if not p:
            continue
        # 提取 header [Company Year section p.NN]
        m = re.match(r"^\d+\.\[([^\]]+)\](.*)", p, flags=re.DOTALL)
        if not m:
            continue
        header, text = m.group(1), m.group(2)
        # header 里最后一段是 p.NN
        pm = re.search(r"p\.(-?\d+)", header)
        page = int(pm.group(1)) if pm else -1
        # section 是倒数第 2 段（如果有）
        parts_h = header.rsplit(" ", 2)
        section = parts_h[-2] if len(parts_h) >= 2 else ""
        chunks.append({"header": header, "page": page, "section": section, "text": text.strip()[:1500]})
    return chunks


async def retrieve(question: str, filter_hint: dict) -> tuple[list[dict], float]:
    """返回 (chunks, elapsed_seconds)"""
    t0 = time.perf_counter()
    vec = (await file_service.embeddings_aliyun([question], normalize=True))[0].embedding
    res = await file_service.search_shared_database(
        user_question=question, question_vector=vec, top_k=TOP_K, **filter_hint
    )
    elapsed = time.perf_counter() - t0
    return parse_chunks_from_search(res), elapsed, res["searchDocText"]


async def call_llm(question: str, context: str) -> tuple:
    """调 qwen-plus（走 chat_service 已配置的 OpenAI 兼容客户端）"""
    t0 = time.perf_counter()
    system_prompt = (
        "你是一个专业的财报问答助手。请严格根据提供的知识库文档内容回答问题，"
        "回答时保留原文数字（不做单位换算）。"
        "如果问题与金融/财报无关（如天气/生活闲聊），请回复：抱歉，我只能回答财报相关问题。"
        "如果用户在寻求投资建议（买/卖/持有股票的建议），请回复：抱歉，我不提供投资建议。"
    )
    # 用同步 openai 客户端（chat_service 已有），放到线程池防阻塞
    loop = asyncio.get_event_loop()
    def _sync_call():
        resp = chat_service.openai.chat.completions.create(
            model="qwen-plus",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": context},
            ],
            temperature=0.1,
        )
        return resp.choices[0].message.content
    ans = await loop.run_in_executor(None, _sync_call)
    return ans, time.perf_counter() - t0


def eval_context_recall(chunks: list[dict], gt_pages: list[int]) -> float:
    """gt_pages ∩ retrieved_pages / len(gt_pages)。若 gt_pages 为空返回 -1（不适用）"""
    if not gt_pages:
        return -1.0
    got = {c["page"] for c in chunks}
    hit = len(set(gt_pages) & got)
    return hit / len(set(gt_pages))


def eval_context_recall_soft(chunks: list[dict], gt_pages: list[int], tol: int = 1) -> float:
    """允许 ±tol 页误差（chunk 可能跨页起始页略偏）"""
    if not gt_pages:
        return -1.0
    got_pages = [c["page"] for c in chunks]
    hit = 0
    for gp in set(gt_pages):
        if any(abs(gp - rp) <= tol for rp in got_pages):
            hit += 1
    return hit / len(set(gt_pages))


def eval_section_hit(chunks: list[dict], expected: str | None) -> float:
    if not expected:
        return -1.0
    if not chunks:
        return 0.0
    return sum(1 for c in chunks if c["section"] == expected) / len(chunks)


def eval_number_precision(answer: str, qa: dict) -> float:
    """检查 answer 是否含 expected_key_facts_regex 或任一 expected_key_facts。"""
    if qa.get("should_refuse"):
        return -1.0
    rgx = qa.get("expected_key_facts_regex")
    if rgx:
        return 1.0 if re.search(rgx, answer, re.I) else 0.0
    facts = qa.get("expected_key_facts", [])
    if not facts:
        return -1.0
    hit = sum(1 for f in facts if f.lower() in answer.lower())
    return hit / len(facts)


REFUSAL_HINTS = [
    "无法", "不能", "抱歉", "cannot", "unable", "not able", "sorry",
    "无关", "off-topic", "投资建议", "investment advice", "not provide",
    "outside", "超出", "无法提供", "不提供", "not a financial",
]


def eval_refusal(answer: str, should_refuse: bool) -> float:
    hinted = any(h in answer.lower() for h in REFUSAL_HINTS)
    if should_refuse:
        return 1.0 if hinted else 0.0
    else:
        # 不应拒答但拒答了 = 0；不应拒答且没拒答 = 1
        return 0.0 if hinted else 1.0


async def run_one(qa: dict, no_llm: bool = False):
    print(f"\n{'='*100}\n[{qa['qid']}] {qa['category']} | {qa['question']}")
    print(f"    filter: {qa['filter_hint']}  gt_pages: {qa.get('ground_truth_pages')}")

    chunks, retr_ms, ctx = await retrieve(qa["question"], qa["filter_hint"])
    print(f"    retrieved {len(chunks)} chunks in {retr_ms*1000:.0f}ms; pages={[c['page'] for c in chunks]} sections={[c['section'] for c in chunks]}")

    recall = eval_context_recall(chunks, qa.get("ground_truth_pages") or [])
    recall_soft = eval_context_recall_soft(chunks, qa.get("ground_truth_pages") or [], tol=1)
    sec_hit = eval_section_hit(chunks, qa.get("expected_section"))

    answer, llm_ms = "", 0.0
    if not no_llm:
        try:
            answer, llm_ms = await call_llm(qa["question"], ctx)
        except Exception as e:
            answer = f"[LLM ERROR: {e}]"
        print(f"    LLM ans ({llm_ms*1000:.0f}ms): {answer[:200]}")

    num_prec = eval_number_precision(answer, qa) if not no_llm else -1.0
    refusal_ok = eval_refusal(answer, qa.get("should_refuse", False)) if not no_llm else -1.0

    result = {
        "qid": qa["qid"], "category": qa["category"], "difficulty": qa["difficulty"],
        "question": qa["question"], "should_refuse": qa.get("should_refuse", False),
        "gt_pages": qa.get("ground_truth_pages"), "expected_section": qa.get("expected_section"),
        "retrieved_pages": [c["page"] for c in chunks],
        "retrieved_sections": [c["section"] for c in chunks],
        "context_recall": recall, "context_recall_soft": recall_soft,
        "section_hit": sec_hit,
        "answer": answer, "number_precision": num_prec, "refusal_ok": refusal_ok,
        "retrieval_ms": retr_ms * 1000, "llm_ms": llm_ms * 1000,
    }
    print(f"    ⇒ recall={recall:.2f} (soft={recall_soft:.2f}) section_hit={sec_hit} num_prec={num_prec} refusal_ok={refusal_ok}")
    return result


def summarize(results: list[dict]) -> dict:
    """聚合指标。-1 表示 N/A 不参与统计。"""
    def avg(xs):
        xs = [x for x in xs if x >= 0]
        return sum(xs) / len(xs) if xs else -1

    non_refusal = [r for r in results if not r["should_refuse"]]
    return {
        "n_total": len(results),
        "n_non_refusal": len(non_refusal),
        "context_recall_strict": avg([r["context_recall"] for r in results]),
        "context_recall_soft":   avg([r["context_recall_soft"] for r in results]),
        "section_hit":           avg([r["section_hit"] for r in results]),
        "number_precision":      avg([r["number_precision"] for r in results]),
        "refusal_ok":            avg([r["refusal_ok"] for r in results]),
        "retrieval_ms_p50":      sorted([r["retrieval_ms"] for r in results])[len(results)//2],
        "retrieval_ms_p95":      sorted([r["retrieval_ms"] for r in results])[int(len(results)*0.95)] if len(results) >= 5 else 0,
        "llm_ms_p50":            sorted([r["llm_ms"] for r in results])[len(results)//2],
        "llm_ms_p95":            sorted([r["llm_ms"] for r in results])[int(len(results)*0.95)] if len(results) >= 5 else 0,
    }


def render_report(results: list[dict], summary: dict) -> str:
    lines = ["# Golden QA v0 评测报告", ""]
    lines.append(f"- QA 集: `evaluation/golden_qa_v0.jsonl` ({summary['n_total']} 题)")
    lines.append(f"- Top-K: {TOP_K}")
    lines.append(f"- 生成时间: {time.strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append("")
    lines.append("## 一、聚合指标")
    lines.append("")
    lines.append("| 指标 | 数值 | 说明 |")
    lines.append("|------|------|------|")
    lines.append(f"| Context Recall (strict) | {summary['context_recall_strict']*100:.1f}% | GT 页需精确出现在召回结果里 |")
    lines.append(f"| Context Recall (soft ±1) | {summary['context_recall_soft']*100:.1f}% | 允许 ±1 页偏移（chunk 起始页可能略移） |")
    lines.append(f"| Section Hit Rate | {summary['section_hit']*100:.1f}% | 有 expected_section 的题里，召回 chunk 落在正确 section 的比例 |")
    lines.append(f"| 数字精确率 | {summary['number_precision']*100:.1f}% | LLM 答案含 expected_key_facts_regex |")
    lines.append(f"| 拒答正确率 | {summary['refusal_ok']*100:.1f}% | should_refuse=true 是否真的拒答；否是否正常答 |")
    lines.append(f"| 检索延迟 P50 / P95 | {summary['retrieval_ms_p50']:.0f}ms / {summary['retrieval_ms_p95']:.0f}ms | Milvus + 关键词过滤 |")
    lines.append(f"| LLM 延迟 P50 / P95 | {summary['llm_ms_p50']:.0f}ms / {summary['llm_ms_p95']:.0f}ms | qwen-plus 非流式 |")
    lines.append("")
    lines.append("## 二、分类聚合")
    lines.append("")
    cats = {}
    for r in results:
        cats.setdefault(r["category"], []).append(r)
    lines.append("| 类别 | 题数 | Recall (soft) | Section Hit | Num Precision | Refusal OK |")
    lines.append("|------|-----|---------------|-------------|---------------|------------|")
    def pct(xs):
        xs = [x for x in xs if x >= 0]
        return f"{sum(xs)/len(xs)*100:.0f}%" if xs else "N/A"
    for cat, rs in cats.items():
        rec = [r["context_recall_soft"] for r in rs]
        sec = [r["section_hit"] for r in rs]
        num = [r["number_precision"] for r in rs]
        ref = [r["refusal_ok"] for r in rs]
        lines.append(f"| {cat} | {len(rs)} | {pct(rec)} | {pct(sec)} | {pct(num)} | {pct(ref)} |")
    lines.append("")
    lines.append("## 三、逐题明细")
    lines.append("")
    lines.append("| QID | 类别 | 问题 | Recall/Soft | Sec Hit | Num | Ref | 检索(ms) | LLM(ms) |")
    lines.append("|-----|------|------|-------------|---------|-----|-----|---------|---------|")
    for r in results:
        q = r["question"][:40] + ("..." if len(r["question"]) > 40 else "")
        fmt = lambda x: f"{x:.2f}" if x >= 0 else "-"
        lines.append(f"| {r['qid']} | {r['category']} | {q} | {fmt(r['context_recall'])}/{fmt(r['context_recall_soft'])} "
                     f"| {fmt(r['section_hit'])} | {fmt(r['number_precision'])} | {fmt(r['refusal_ok'])} "
                     f"| {r['retrieval_ms']:.0f} | {r['llm_ms']:.0f} |")
    lines.append("")
    lines.append("## 四、Bad Cases（Recall soft < 1.0 或 Num Precision < 1.0）")
    lines.append("")
    for r in results:
        if (r["context_recall_soft"] >= 0 and r["context_recall_soft"] < 1.0) or \
           (r["number_precision"] >= 0 and r["number_precision"] < 1.0):
            lines.append(f"### [{r['qid']}] {r['question']}")
            lines.append(f"- 期望页: `{r['gt_pages']}`  期望 section: `{r['expected_section']}`")
            lines.append(f"- 召回页: `{r['retrieved_pages']}`  召回 section: `{r['retrieved_sections']}`")
            lines.append(f"- Recall strict={r['context_recall']:.2f}, soft={r['context_recall_soft']:.2f}")
            lines.append(f"- Num precision={r['number_precision']:.2f}")
            lines.append(f"- LLM 答案: {r['answer'][:400]}")
            lines.append("")
    return "\n".join(lines)


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", type=str, help="只跑指定 QID，逗号分隔")
    ap.add_argument("--no-llm", action="store_true", help="只跑检索评测，不调 LLM")
    args = ap.parse_args()

    connect_milvus()
    qas = load_qa()
    if args.only:
        picked = set(args.only.split(","))
        qas = [q for q in qas if q["qid"] in picked]
    print(f"Loaded {len(qas)} QA items")

    results = []
    with open(RAW_OUT_PATH, "w", encoding="utf-8") as raw_f:
        for qa in qas:
            r = await run_one(qa, no_llm=args.no_llm)
            results.append(r)
            raw_f.write(json.dumps(r, ensure_ascii=False) + "\n")
            raw_f.flush()

    summary = summarize(results)
    report = render_report(results, summary)
    REPORT_PATH.write_text(report, encoding="utf-8")
    print("\n" + "="*80)
    print(f"SUMMARY: {json.dumps(summary, indent=2)}")
    print(f"\nReport → {REPORT_PATH}")
    print(f"Raw    → {RAW_OUT_PATH}")


if __name__ == "__main__":
    asyncio.run(main())
