"""
Replay 前端验证中出问题的场景（2/3/6/7），只跑检索、不走 LLM。
目的：坐实"是检索没命中/命中不含答案 → LLM 用先验知识幻觉"的根因。
"""
import sys, os, asyncio, re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "rag-fastapi"))
os.chdir(ROOT / "rag-fastapi")

from pymilvus import connections
from app.config import settings
from app.file.service import file_service


def connect():
    h, p = settings.MILVUS_ADDRESS.split(":")
    connections.connect(alias="default", host=h, port=p)


async def replay(label, question, **filters):
    top_k = filters.pop("top_k", 5)
    print("\n" + "=" * 100)
    print(f"【{label}】 {question}")
    if filters:
        print(f"    Filter: {filters}")
    print("=" * 100)

    vec = (await file_service.embeddings_aliyun([question], normalize=True))[0].embedding
    res = await file_service.search_shared_database(
        user_question=question, question_vector=vec, top_k=top_k, **filters,
    )
    titles = res["searchDocTitle"]
    body = res["searchDocText"].split("文档内容:", 1)[-1]

    print(f"\n>> 命中 {len(titles)} 份文档:")
    for t in titles:
        print(f"   - {t}")

    chunks = re.split(r"^\d+\.", body, flags=re.MULTILINE)
    chunks = [c.strip() for c in chunks if c.strip() and c.strip() != "&没有检索到相关文档&"]
    print(f"\n>> 命中 chunks（前 3 条，每条截 700 字）:")
    for i, ch in enumerate(chunks[:3], 1):
        print(f"\n--- chunk {i} ---")
        print(ch[:700])
        if len(ch) > 700:
            print(f"...[还有 {len(ch)-700} 字]")


async def main():
    connect()

    # 场景 3：biotech 列表（LLM 编造了 CRISPR/Intellia/Beam/Editas，这些库里没有）
    await replay(
        "S3 biotech list",
        "List all biotech companies in the knowledge base",
        top_k=8,
    )

    # 场景 6：投资建议 + 数字编造（合规红线）
    await replay(
        "S6 buy MSFT?",
        "Based on Microsoft's financials, should I buy MSFT stock?",
        top_k=5,
    )

    # 场景 7：Weis Markets（中文 → 检索完全没命中 → LLM 全靠记忆瞎编）
    await replay(
        "S7 Weis Markets (zh)",
        "Weis Markets 是做什么业务的？",
        top_k=5,
    )

    # 附加：加过滤看看能不能救 S7
    await replay(
        "S7' Weis Markets (with ticker filter)",
        "Weis Markets 是做什么业务的？",
        ticker="WMK",
        top_k=5,
    )


if __name__ == "__main__":
    asyncio.run(main())
