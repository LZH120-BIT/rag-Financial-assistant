"""Quick sanity check: process 3 PDFs, print result"""
import sys
sys.path.insert(0, "scripts")
from pathlib import Path
from build_metadata import process_one, GOLDEN_DIR
import json, random

random.seed(42)
pdfs = sorted(GOLDEN_DIR.glob("*.pdf"))
sample = random.sample(pdfs, 3)
print(f"Testing on 3 samples from {len(pdfs)} PDFs\n")
for p in sample:
    print(f"→ {p.name}")
    r = process_one(p)
    print(json.dumps(r, ensure_ascii=False, indent=2))
    print()
