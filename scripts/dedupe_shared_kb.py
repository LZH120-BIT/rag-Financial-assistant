"""
清理共享 collection 中的重复入库数据。
策略：对每个 docHash，保留 pk 最大的那组（最新入库），删除更早的 pk 组。

如何识别"一次入库"：由于 auto_id 是单调递增，同一次入库的所有 pk 是连续的一段。
用 pk 排序后取相邻差值，找到每个 hash 的"入库轮次"，只保留最后一轮。
"""
import sys, os
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "rag-fastapi"))
os.chdir(ROOT / "rag-fastapi")

from pymilvus import connections, Collection
from app.config import settings
from collections import defaultdict

h, p = settings.MILVUS_ADDRESS.split(":")
connections.connect(host=h, port=p)
c = Collection("golden_100_reports")
c.load()
c.flush()

# 拉全部 (pk, docHash)
it = c.query_iterator(expr='docHash != ""', output_fields=["docHash"], batch_size=5000)
pk_by_hash = defaultdict(list)
total = 0
while True:
    batch = it.next()
    if not batch: break
    for r in batch:
        pk_by_hash[r["docHash"]].append(r["id"])
    total += len(batch)
it.close()
print(f"总 entities: {total}")
print(f"unique hash: {len(pk_by_hash)}")

# 对每个 hash 找"入库轮次"：pk 排序后，相邻差 > 10000 的算断点（分开的入库）
to_delete = []  # 要删的 pk
kept = 0
for hh, pks in pk_by_hash.items():
    pks_sorted = sorted(pks)
    # 找断点
    groups = [[pks_sorted[0]]]
    for pk in pks_sorted[1:]:
        if pk - groups[-1][-1] > 10000:  # 断点阈值：一次入库的 pk 连续，间隔通常 <200
            groups.append([pk])
        else:
            groups[-1].append(pk)
    # 保留最后一组（最新一次入库），删掉之前所有组
    if len(groups) > 1:
        for g in groups[:-1]:
            to_delete.extend(g)
    kept += len(groups[-1])

print(f"要保留: {kept}")
print(f"要删除: {len(to_delete)}")

if to_delete:
    # Milvus delete 支持 in 表达式，分批删（每批 <= 2000 个）
    batch_size = 1000
    for i in range(0, len(to_delete), batch_size):
        batch = to_delete[i:i + batch_size]
        expr = f"id in {batch}"
        c.delete(expr)
        print(f"  删批 {i // batch_size + 1}/{(len(to_delete) + batch_size - 1) // batch_size}: {len(batch)} 条")
    c.flush()
    print(f"✅ 删除完毕，flush 后 num_entities = {c.num_entities}")
else:
    print("无重复，跳过删除")

c.release()
