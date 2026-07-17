"""
15 个 sample query 全面验证 RAG 检索效果。
分 5 类：
  A. 精确数字查询（考验能不能命中财务报表 chunk）
  B. 中英文跨语言查询（考验双端归一化）
  C. section 过滤（考验 metadata 精确定位）
  D. 跨公司对比（考验无 filter 的跨库检索）
  E. 定性/描述性问题（考验 MD&A / Risk 语义召回）

对每个 query：打印 filter、命中文档、命中 chunks (前 2 条，各截 800 字)。
"""
import sys, os, asyncio, re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "rag-fastapi"))
os.chdir(ROOT / "rag-fastapi")

from pymilvus import connections, Collection
from app.config import settings
from app.file.service import file_service


def connect():
    h, p = settings.MILVUS_ADDRESS.split(":")
    connections.connect(alias="default", host=h, port=p)


async def run(qid, question, **filters):
    """跑一个 query，返回结构化结果。"""
    top_k = filters.pop("top_k", 5)
    print("\n" + "=" * 100)
    print(f"【Q{qid}】 {question}")
    if filters:
        print(f"    Filter: {filters}")
    print("=" * 100)

    vec = (await file_service.embeddings_aliyun([question], normalize=True))[0].embedding
    res = await file_service.search_shared_database(
        user_question=question, question_vector=vec,
        top_k=top_k, **filters,
    )
    titles = res["searchDocTitle"]
    body = res["searchDocText"].split("文档内容:", 1)[-1]

    print(f"\n>> 命中 {len(titles)} 份文档:")
    for t in titles:
        print(f"   - {t}")

    # 提取前 2 条 chunks 展示
    chunks = re.split(r"^\d+\.", body, flags=re.MULTILINE)
    chunks = [c.strip() for c in chunks if c.strip() and c.strip() != "&没有检索到相关文档&"]
    print(f"\n>> 命中 chunks（前 2 条，每条截 900 字）:")
    for i, ch in enumerate(chunks[:2], 1):
        print(f"\n--- chunk {i} ---")
        print(ch[:900])
        if len(ch) > 900:
            print(f"...[还有 {len(ch)-900} 字]")


async def main():
    connect()

    # === A. 精确数字查询（跨公司 top-k 命中最能匹配的公司） ===
    await run("A1", "What was Microsoft's total revenue in fiscal year 2022?",
              company="Microsoft Corporation", top_k=3)
    await run("A2", "What was Weis Markets' net income in 2022?",
              company="Weis Markets, Inc.", top_k=3)
    await run("A3", "Microsoft 的研发费用 (R&D expenses) 是多少？",
              company="Microsoft Corporation", top_k=3)

    # === B. 中英文跨语言查询 ===
    await run("B1", "这家公司的主要业务是什么？",
              company="Microsoft Corporation", top_k=3)
    await run("B2", "东芝公司 2022 年营收情况",
              company="Toshiba Corporation", top_k=3)
    await run("B3", "How does the company recognize revenue?",
              company="Microsoft Corporation", top_k=3)

    # === C. section 过滤精准定位 ===
    await run("C1", "What are the main risk factors?",
              company="Microsoft Corporation", section="risk", top_k=3)
    await run("C2", "Who signed the independent auditor's report?",
              company="Microsoft Corporation", section="audit", top_k=3)
    await run("C3", "Show me the consolidated balance sheet.",
              company="Microsoft Corporation", section="financials", top_k=3)

    # === D. 跨公司对比（无 filter） ===
    await run("D1", "Which company reported the highest R&D expenses?", top_k=5)
    await run("D2", "biotechnology company annual report", top_k=5)
    await run("D3", "banking institution net interest income", top_k=5)

    # === E. 定性/描述性问题 ===
    await run("E1", "How did the pandemic affect the company's operations?",
              company="Microsoft Corporation", top_k=3)
    await run("E2", "What is the company's ESG or sustainability strategy?",
              company="Microsoft Corporation", top_k=3)
    await run("E3", "Describe any cybersecurity incidents disclosed.",
              company="Microsoft Corporation", top_k=3)


if __name__ == "__main__":
    asyncio.run(main())
