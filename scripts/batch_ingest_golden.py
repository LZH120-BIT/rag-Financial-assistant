"""
批量入库 golden_set/ 下的 101 份财报 PDF 到共享 collection `golden_100_reports`。

用法（在项目根跑）：
    cd rag-fastapi && python3 -m scripts.batch_ingest_golden [--limit N] [--only HASH] [--dry-run] [--force]

设计要点：
- **可断点续传**：启动前查 collection 已入库的 docHash，默认跳过；--force 强制重入。
- **失败隔离**：单个 PDF 失败追加日志到 scripts/ingest_log.jsonl，继续下一个。
- **顺序执行**：避免同时冲击 embedding API rate limit（DashScope）。
- **完整 pipeline**：OCR (含 M1-M5 特殊内容处理) → chunk (1500/200, 财报友好分隔符) → embed → 入库。
- **进度提示**：每份 PDF 打印一行简明状态，末尾出总表。

启动前置：
- Milvus / MongoDB 已启动（docker-compose）
- .env 里 TONGYI_AKI_KEY 已配置
"""
import sys
import os
import json
import time
import asyncio
import argparse
from pathlib import Path
from typing import Optional

# 支持 `cd rag-fastapi && python3 -m scripts.batch_ingest_golden` 和
# 也支持 `python3 rag-fastapi/../scripts/batch_ingest_golden.py`（backup path）
ROOT = Path(__file__).resolve().parent.parent  # 项目根
FASTAPI_DIR = ROOT / "rag-fastapi"
if str(FASTAPI_DIR) not in sys.path:
    sys.path.insert(0, str(FASTAPI_DIR))

# 环境变量（避免依赖 .env 加载顺序）
os.chdir(FASTAPI_DIR)

from pymilvus import connections, utility, Collection
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_core.documents import Document as LangchainDocument

from app.config import settings
from app.file.service import file_service
from app.file import ocr as ocr_module


GOLDEN_DIR = ROOT / "golden_set"
METADATA_FILE = GOLDEN_DIR / "metadata.json"
LOG_FILE = ROOT / "scripts" / "ingest_log.jsonl"

# Chunk 参数：与 file/service.py:read_file 完全一致
CHUNK_SIZE = 1500
CHUNK_OVERLAP = 200
CHUNK_SEPARATORS = ["\n---\n", "\n[section=", "\n\n", "\n", "。", ".", " ", ""]


def log(entry: dict):
    """追加一行 JSONL 日志。"""
    entry["ts"] = time.strftime("%Y-%m-%d %H:%M:%S")
    with LOG_FILE.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def connect_milvus():
    """建 Milvus 连接。参数从 settings.MILVUS_ADDRESS 读（"host:port" 格式）。"""
    address = settings.MILVUS_ADDRESS
    host, port = address.split(":")
    connections.connect(alias="default", host=host, port=port)
    print(f"[batch] ✅ Milvus connected: {address}")


def query_existing_hashes(collection_name: str) -> set:
    """查已入库的 docHash 集合，支持断点续传。
    用 query_iterator 全量遍历。query() 的 limit 是返回条数上限，不是分页 offset —
    如果 collection 有几万 entities，单次 query(limit=N) 只拿前 N 条，去重后不足以覆盖所有 docHash，
    会导致误判"未入库"重复入。
    """
    if not utility.has_collection(collection_name):
        return set()
    coll = Collection(name=collection_name)
    coll.load()
    try:
        it = coll.query_iterator(expr='docHash != ""', output_fields=["docHash"], batch_size=5000)
        hashes = set()
        while True:
            batch = it.next()
            if not batch:
                break
            for r in batch:
                if r.get("docHash"):
                    hashes.add(r["docHash"])
        it.close()
        return hashes
    except Exception as e:
        print(f"[batch] ⚠️  query existing hashes 失败（可能是空集合）: {e}")
        return set()
    finally:
        coll.release()


async def ingest_one(pdf_path: Path, meta: dict, force: bool = False) -> dict:
    """入库一个 PDF。返回 {status, ...} dict。"""
    doc_hash = meta["hash"]
    ticker = meta.get("ticker") or "UNKNOWN"
    year = meta.get("fiscal_year") or 0
    company = meta.get("company") or "UNKNOWN"
    file_name = f"{company} {year} {meta.get('report_type', '')}".strip()

    print(f"\n[batch] ▶ {ticker} {year} | {company[:50]} | {pdf_path.name} ({meta.get('page_count')}p, {meta.get('file_size_kb')}KB)")
    t0 = time.time()

    try:
        # 1. OCR (走完整 M1-M5 pipeline)
        full_text = await ocr_module.recognize(str(pdf_path), "application/pdf")
        if not full_text or len(full_text) < 100:
            return {"status": "empty_ocr", "hash": doc_hash, "ticker": ticker, "text_len": len(full_text or "")}

        # 2. Chunk
        splitter = RecursiveCharacterTextSplitter(
            chunk_size=CHUNK_SIZE, chunk_overlap=CHUNK_OVERLAP,
            separators=CHUNK_SEPARATORS,
        )
        chunks = splitter.create_documents([full_text])
        print(f"[batch]   OCR done: {len(full_text)} chars → {len(chunks)} chunks (elapsed {time.time()-t0:.1f}s)")

        # 3. 入库（doc_id 直接用 hash 保证幂等；重入前先删旧记录）
        doc_id = doc_hash  # hash 作为 docId，唯一
        if force:
            # 先删掉旧数据（幂等）
            coll_name = file_service.SHARED_KB_COLLECTION
            if utility.has_collection(coll_name):
                coll = Collection(name=coll_name)
                coll.load()
                coll.delete(f'docHash == "{doc_hash}"')
                coll.release()
                print(f"[batch]   --force: 已删除旧 chunks (docHash={doc_hash[:8]}...)")

        await file_service.vector_storage_shared(
            file_name=file_name,
            split_document=chunks,
            doc_id=doc_id,
            meta=meta,
        )

        elapsed = time.time() - t0
        print(f"[batch]   ✅ 完成 elapsed={elapsed:.1f}s")
        return {"status": "ok", "hash": doc_hash, "ticker": ticker, "chunks": len(chunks), "elapsed": round(elapsed, 1)}

    except Exception as e:
        import traceback
        traceback.print_exc()
        return {"status": "error", "hash": doc_hash, "ticker": ticker, "error": str(e)}


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=0, help="只处理前 N 份（0=全部）")
    parser.add_argument("--only", type=str, default=None, help="只处理指定 hash")
    parser.add_argument("--dry-run", action="store_true", help="只列出将处理的文件，不入库")
    parser.add_argument("--force", action="store_true", help="强制重入（覆盖已入库的）")
    args = parser.parse_args()

    # 读 metadata
    meta_list = json.loads(METADATA_FILE.read_text(encoding="utf-8"))
    print(f"[batch] 加载 metadata: {len(meta_list)} 份 PDF")

    # 连 Milvus + 查已入库（--dry-run 时跳过）
    if not args.dry_run:
        connect_milvus()
        coll_name = file_service.SHARED_KB_COLLECTION
        existing = query_existing_hashes(coll_name) if not args.force else set()
        if existing:
            print(f"[batch] 已入库 {len(existing)} 份，本次将跳过（除非 --force）")
    else:
        existing = set()

    # 过滤 & 排序（小的先跑，出问题早发现）
    todo = []
    for m in meta_list:
        if args.only and m["hash"] != args.only:
            continue
        if not args.force and m["hash"] in existing:
            continue
        pdf_path = GOLDEN_DIR / m["filename"]
        if not pdf_path.exists():
            print(f"[batch] ⚠️  文件缺失: {pdf_path}")
            continue
        todo.append((pdf_path, m))
    todo.sort(key=lambda x: x[1].get("page_count", 999))

    if args.limit:
        todo = todo[:args.limit]

    print(f"[batch] 待处理 {len(todo)} 份 PDF")
    if args.dry_run:
        for pdf_path, m in todo:
            print(f"  - {m.get('ticker')} {m.get('fiscal_year')} {m['filename']} ({m.get('page_count')}p)")
        return

    # 顺序处理
    results = {"ok": 0, "error": 0, "empty_ocr": 0}
    t_start = time.time()
    for i, (pdf_path, m) in enumerate(todo, 1):
        print(f"\n════════════════ {i}/{len(todo)} ════════════════")
        r = await ingest_one(pdf_path, m, force=args.force)
        log(r)
        results[r["status"]] = results.get(r["status"], 0) + 1

    total_elapsed = time.time() - t_start
    print(f"\n════════════════ 汇总 ════════════════")
    print(f"总耗时: {total_elapsed:.0f}s ({total_elapsed/60:.1f}min)")
    print(f"结果: {results}")
    print(f"日志: {LOG_FILE}")


if __name__ == "__main__":
    asyncio.run(main())
