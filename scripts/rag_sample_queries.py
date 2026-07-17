"""
用已入库的 5 份财报做 sample query，验证 RAG 命中效果。
"""
import sys
import os
import asyncio
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FASTAPI_DIR = ROOT / "rag-fastapi"
sys.path.insert(0, str(FASTAPI_DIR))
os.chdir(FASTAPI_DIR)

from pymilvus import connections, Collection
from app.config import settings
from app.file.service import file_service


def connect():
    host, port = settings.MILVUS_ADDRESS.split(":")
    connections.connect(alias="default", host=host, port=port)


async def query_and_report(question: str, company: str = None, section: str = None, year: int = None, top_k: int = 5):
    print("\n" + "=" * 90)
    print(f"❓ Query: {question}")
    if company or section or year:
        print(f"   Filter: company={company} section={section} year={year}")
    print("=" * 90)

    # 走标准流程：query embed（normalize=True）→ hybrid search + metadata 过滤
    vec_res = await file_service.embeddings_aliyun([question], normalize=True)
    q_vec = vec_res[0].embedding

    result = await file_service.search_shared_database(
        user_question=question,
        question_vector=q_vec,
        company=company,
        section=section,
        year=year,
        top_k=top_k,
    )

    titles = result["searchDocTitle"]
    text = result["searchDocText"]
    print(f"\n📊 命中文档标题 ({len(titles)}):")
    for t in titles:
        print(f"   - {t}")

    # 把 searchDocText 截断展示前 2000 字看命中的实际内容
    body = text.split("文档内容:", 1)[-1]
    print(f"\n📄 命中 chunks（前 3000 字）:")
    print(body[:3000])
    if len(body) > 3000:
        print(f"\n... [还有 {len(body) - 3000} 字省略]")


async def main():
    connect()

    # 看看当前入了哪 5 份
    coll = Collection(name=file_service.SHARED_KB_COLLECTION)
    coll.load()
    res = coll.query(expr="docHash != ''", output_fields=["company", "ticker", "year", "docHash"], limit=16384)
    seen = {}
    for r in res:
        h = r["docHash"]
        if h not in seen:
            seen[h] = (r["company"], r["ticker"], r["year"])
    print(f"[当前共享库已入库 {len(seen)} 份财报]")
    for h, (c, t, y) in seen.items():
        print(f"  - {t or '?'} | {c} | FY{y} | hash={h[:8]}")

    # === 测试 5 个 query，覆盖不同 section / 是否加 filter / 中英文 ===

    # 1. 财务数字类（无 filter，跨公司命中）
    await query_and_report("What was the total revenue in 2022?", top_k=5)

    # 2. 公司过滤 + Risk section
    await query_and_report("What are the main risk factors?", company="Weis Markets, Inc.", section="risk", top_k=5)

    # 3. 中文查询 - 归一化 EN doc（考验双端归一化）
    await query_and_report("这家公司的营业收入是多少？", company="Weis Markets, Inc.", top_k=5)

    # 4. 公司过滤 + Audit section（要求命中审计师意见）
    await query_and_report("Who is the independent auditor?", company="Weis Markets, Inc.", section="audit", top_k=5)

    # 5. MD&A 高层描述
    await query_and_report("How did management describe the company's performance?", company="Weis Markets, Inc.", section="mda", top_k=5)


if __name__ == "__main__":
    asyncio.run(main())
