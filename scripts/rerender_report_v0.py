"""从 run_v0_raw.jsonl 重新生成报告（不重跑 LLM）"""
import sys, json
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

# 复用 render 函数
from run_eval_v0 import render_report, summarize

results = [json.loads(l) for l in open(ROOT/"evaluation/run_v0_raw.jsonl", encoding="utf-8")]
summary = summarize(results)
report = render_report(results, summary)

# 追加分析结论 section
analysis = """

---

## 五、分析结论（人工解读）

### 5.1 关键洞察：Recall 指标严重低估真实表现

聚合数字乍看很难看：
- Context Recall soft 仅 25%
- Section Hit Rate 27%

但**实际问答表现极强**：
- **数字精确率 83%**（15/18 非拒答题里，LLM 答案里含正确数字）
- **拒答正确率 100%**（Q19 非金融、Q20 投资建议都正确拒答）

### 5.2 Recall 低的三个真实原因（按影响排序）

**原因 A — `pageNum=-1` 结构性问题**（影响 8/18 题）
LLM 答案里频繁出现 `[Microsoft Corporation 2022 body p.-1]` 引用，说明 chunk 命中了正确内容但 `pageNum` 存储为 -1。根因：`_parse_section_from_chunk` 只在 chunk 首 200 字符找 marker，但 text splitter 可能把 marker 切到 chunk 中间或干脆丢失。这是**系统 bug**，非造题问题。

**原因 B — GT pages 覆盖不全**（影响 4 题：Q01/Q02/Q03/Q18）
造题时我把 MSFT 财务题的 gt_pages 只标了 [49,50]（Income Statement 页），但同样的数字 $198,270M 在 MSFT PDF 里出现在 4 个页面：**p.35（MDA 汇总表 SUMMARY RESULTS OF OPERATIONS）、p.36、p.49、p.84（audit CAM）**。LLM 命中了 p.35 的汇总表（正确答案！），但因为不在我标的 gt_pages 里被算成 recall=0。**这是造题失误**，v1 版会修。

**原因 C — WMK 财报数字表未召回**（真实 bug：Q04/Q05）
Weis Markets net sales / total assets 两题，召回全是 body 段落（业务描述、门店数），完全没有 income statement / balance sheet 表格 chunk 命中。LLM 只能回答"没有相关数据"。这暴露一个真实问题：**小公司财报的数字表 chunk embedding 弱于业务描述 chunk**。原因猜测：
- WMK 的财报表 chunk 里数字密度高、语义描述弱 → embedding 稀释
- 未识别为 `section=financials`，无法用 metadata 加权
- Chunk splitter 可能把表格切碎

这与 Known Limitation L2 一致。

### 5.3 表现好的题（无争议）

- **Q09/Q10/Q11 三个 section=audit / section=financials 过滤题**：Section Hit 100%，Recall soft 100%，说明**section metadata 机制在标准结构 PDF 上工作正常**
- **Q12/Q13 跨公司对比题**：LLM 都答对（列出 biotech / bank 名字）
- **Q15/Q17 定性题**：Microsoft 环保战略 / 网络安全风险都答得很详细正确
- **Q19/Q20 拒答题**：完全按 system prompt 拒答

### 5.4 v1 版改进建议（等老师题过来后）

1. **修 `pageNum=-1` 结构性问题**：调整 `_parse_section_from_chunk` 兼容 marker 缺失情况（如从 chunk 全文找 marker、或用回退启发式）
2. **修 WMK 数字表召回**：分析 WMK PDF 的 financials 页为什么没打上 section 标签，考虑对财务表 chunk 单独增强 embedding（如加"[TABLE]"前缀）
3. **修 QA gt_pages 完整性**：脚本自动扫描 PDF 里"含 expected_key_facts 的所有页"，作为 GT 页扩展
4. **接入 LLM Judge（Answer Correctness）**：现在的数字精确率是硬正则，答案组织质量没评上；接 qwen-max 做裁判打 0/0.5/1 分

### 5.5 一句话总结

**系统实际问答质量优于 Recall 数字显示**（83% 数字准确、100% 拒答），Recall 低主要是造题失误 + `pageNum=-1` 存储 bug。真实检索问题只 Q04/Q05 两题（小公司财报数字表召回失败），归入 Known Limitation L2。这份 v0 评测**够用来汇报"系统已可用、待优化点已定位"**，等老师题过来后再做 v1。
"""
report += analysis
(ROOT/"evaluation/report_v0.md").write_text(report, encoding="utf-8")
print("regenerated:", ROOT/"evaluation/report_v0.md")
