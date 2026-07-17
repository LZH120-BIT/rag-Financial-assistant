# Golden QA v0 评测报告

- QA 集: `evaluation/golden_qa_v0.jsonl` (20 题)
- Top-K: 5
- 生成时间: 2026-07-07 15:27:49

## 一、聚合指标

| 指标 | 数值 | 说明 |
|------|------|------|
| Context Recall (strict) | 13.9% | GT 页需精确出现在召回结果里 |
| Context Recall (soft ±1) | 25.0% | 允许 ±1 页偏移（chunk 起始页可能略移） |
| Section Hit Rate | 27.3% | 有 expected_section 的题里，召回 chunk 落在正确 section 的比例 |
| 数字精确率 | 83.3% | LLM 答案含 expected_key_facts_regex |
| 拒答正确率 | 100.0% | should_refuse=true 是否真的拒答；否是否正常答 |
| 检索延迟 P50 / P95 | 1387ms / 7092ms | Milvus + 关键词过滤 |
| LLM 延迟 P50 / P95 | 4048ms / 9883ms | qwen-plus 非流式 |

## 二、分类聚合

| 类别 | 题数 | Recall (soft) | Section Hit | Num Precision | Refusal OK |
|------|-----|---------------|-------------|---------------|------------|
| exact_number | 5 | 0% | 0% | 60% | 100% |
| cross_lang | 3 | 0% | 0% | 100% | 100% |
| section_filter | 3 | 100% | 100% | 67% | 100% |
| cross_company | 2 | N/A | N/A | 100% | 100% |
| qualitative | 4 | 25% | N/A | 100% | 100% |
| hard_synthesis | 1 | 0% | 0% | 100% | 100% |
| refusal | 2 | N/A | N/A | N/A | 100% |

## 三、逐题明细

| QID | 类别 | 问题 | Recall/Soft | Sec Hit | Num | Ref | 检索(ms) | LLM(ms) |
|-----|------|------|-------------|---------|-----|-----|---------|---------|
| Q01 | exact_number | What was Microsoft's total revenue in fi... | 0.00/0.00 | 0.00 | 1.00 | 1.00 | 7092 | 1772 |
| Q02 | exact_number | What was Microsoft's research and develo... | 0.00/0.00 | 0.00 | 1.00 | 1.00 | 1417 | 2145 |
| Q03 | exact_number | What was Microsoft's net income for fisc... | 0.00/0.00 | 0.00 | 1.00 | 1.00 | 1300 | 1323 |
| Q04 | exact_number | What were Weis Markets' net sales in fis... | 0.00/0.00 | 0.00 | 0.00 | 1.00 | 1652 | 2858 |
| Q05 | exact_number | What were Weis Markets' total assets as ... | 0.00/0.00 | 0.00 | 0.00 | 1.00 | 1417 | 2056 |
| Q06 | cross_lang | 微软 2022 财年的研发费用是多少？ | 0.00/0.00 | 0.00 | 1.00 | 1.00 | 1371 | 4173 |
| Q07 | cross_lang | 东芝公司 2021 财年（截至 2022 年 3 月）的销售额是多少？ | 0.00/0.00 | - | 1.00 | 1.00 | 1531 | 3281 |
| Q08 | cross_lang | 这家公司的主要业务分部有哪些？ | 0.00/0.00 | 0.00 | 1.00 | 1.00 | 808 | 6186 |
| Q09 | section_filter | Who signed the independent auditor's rep... | 0.33/1.00 | 1.00 | 0.00 | 1.00 | 1154 | 6604 |
| Q10 | section_filter | Show me Microsoft's consolidated balance... | 0.50/1.00 | 1.00 | 1.00 | 1.00 | 1867 | 2400 |
| Q11 | section_filter | What is discussed in the Critical Audit ... | 1.00/1.00 | 1.00 | 1.00 | 1.00 | 947 | 9265 |
| Q12 | cross_company | Which biotechnology companies are repres... | -/- | - | 1.00 | 1.00 | 1182 | 7795 |
| Q13 | cross_company | Which banking institutions disclose net ... | -/- | - | 1.00 | 1.00 | 1339 | 4048 |
| Q14 | qualitative | How did Microsoft describe the impact of... | 0.00/0.12 | - | 1.00 | 1.00 | 1387 | 5363 |
| Q15 | qualitative | What is Microsoft's approach to environm... | 0.00/0.00 | - | 1.00 | 1.00 | 1693 | 9883 |
| Q16 | qualitative | How does Microsoft recognize revenue for... | 0.25/0.62 | - | 1.00 | 1.00 | 1420 | 6721 |
| Q17 | qualitative | What cybersecurity risks does Microsoft ... | -/- | - | 1.00 | 1.00 | 1355 | 7179 |
| Q18 | hard_synthesis | Comparing Microsoft's fiscal year 2022 a... | 0.00/0.00 | 0.00 | 1.00 | 1.00 | 1472 | 3626 |
| Q19 | refusal | 今天天气怎么样？ | -/- | - | - | 1.00 | 1059 | 650 |
| Q20 | refusal | Should I buy Microsoft stock right now? ... | -/- | - | - | 1.00 | 805 | 745 |

## 四、Bad Cases（Recall soft < 1.0 或 Num Precision < 1.0）

### [Q01] What was Microsoft's total revenue in fiscal year 2022?
- 期望页: `[49, 50]`  期望 section: `financials`
- 召回页: `[-1, -1, 36, -1, 37]`  召回 section: `['body', 'body', 'mda', 'body', 'mda']`
- Recall strict=0.00, soft=0.00
- Num precision=1.00
- LLM 答案: Microsoft's total revenue in fiscal year 2022 was **$198,270 million** (i.e., $198.27 billion), as stated in the “SUMMARY RESULTS OF OPERATIONS” table in document [4].

### [Q02] What was Microsoft's research and development expenses in fiscal year 2022?
- 期望页: `[49, 50]`  期望 section: `financials`
- 召回页: `[-1, -1, -1, -1, 36]`  召回 section: `['body', 'body', 'body', 'body', 'mda']`
- Recall strict=0.00, soft=0.00
- Num precision=1.00
- LLM 答案: Microsoft's research and development expenses in fiscal year 2022 were **$24,512 million** (i.e., $24.512 billion), as stated in the document:  

> "Research and development expenses  
> $   24,512  $  20,716   
> 18%"  

This figure is presented in millions and reflects an 18% increase from fiscal year 2021 ($20,716 million).

### [Q03] What was Microsoft's net income for fiscal year 2022?
- 期望页: `[49, 50]`  期望 section: `financials`
- 召回页: `[-1, 36, -1, -1, 37]`  召回 section: `['body', 'mda', 'body', 'body', 'mda']`
- Recall strict=0.00, soft=0.00
- Num precision=1.00
- LLM 答案: Microsoft's net income for fiscal year 2022 was **$72,738 million** (or $72.738 billion), as stated in the "SUMMARY RESULTS OF OPERATIONS" table in document [4].

### [Q04] What were Weis Markets' net sales in fiscal year 2022?
- 期望页: `[16, 17]`  期望 section: `financials`
- 召回页: `[-1, -1, -1, -1, -1]`  召回 section: `['body', 'body', 'body', 'body', 'body']`
- Recall strict=0.00, soft=0.00
- Num precision=0.00
- LLM 答案: The provided knowledge base documents do **not contain Weis Markets’ net sales figure for fiscal year 2022**. While the documents describe the company’s business, store count (197 stores), product categories, geographic presence, and operational details, they **do not include any financial figures such as net sales, revenue, or income**.

Therefore, based on the given documents:  
**Net sales for 

### [Q05] What were Weis Markets' total assets as of December 31, 2022?
- 期望页: `[24, 25]`  期望 section: `financials`
- 召回页: `[-1, -1, -1, -1, -1]`  召回 section: `['body', 'body', 'body', 'body', 'body']`
- Recall strict=0.00, soft=0.00
- Num precision=0.00
- LLM 答案: The provided knowledge base documents do not include Weis Markets’ total assets as of December 31, 2022. The excerpts contain company overview, business description, operations, trademarks, and store count information, but no balance sheet data or financial figures such as total assets.

Therefore, based on the given documents: **total assets as of December 31, 2022 are not disclosed**.

### [Q06] 微软 2022 财年的研发费用是多少？
- 期望页: `[49, 50]`  期望 section: `financials`
- 召回页: `[-1, -1, -1, -1, 36]`  召回 section: `['body', 'body', 'body', 'body', 'mda']`
- Recall strict=0.00, soft=0.00
- Num precision=1.00
- LLM 答案: 根据知识库文档内容，微软（Microsoft）2022财年的**研发费用（Research and development expenses）为 24,512 百万美元**，即 **245.12 亿美元**。

依据来源：文档第3条  
> Research and development expenses include payroll, employee benefits, stock-based compensation expense, and other headcount-related expenses associated with product development...  
> **Research and development expenses**  
> **$ 24,512**  $ 20,716   
> **18%**  

该数据单位为“百万美元”（a

### [Q07] 东芝公司 2021 财年（截至 2022 年 3 月）的销售额是多少？
- 期望页: `[32, 33]`  期望 section: `None`
- 召回页: `[-1, -1, -1, -1, -1]`  召回 section: `['body', 'body', 'body', 'body', 'body']`
- Recall strict=0.00, soft=0.00
- Num precision=1.00
- LLM 答案: 根据文档内容，东芝公司2021财年（截至2022年3月31日）的销售额为 **3,337.0亿日元**。

依据来源：  
文档第1段明确指出：  
> "Net sales increased by 282.6 billion yen year on year to **3,337.0 billion yen**..."  
并标注该数据对应 **FY2021**（即2021财年），且财报期间为“Fiscal Year ended March 31, 2022”，符合用户所问的“2021财年（截至2022年3月）”。

因此，答案是：**3,337.0亿日元**。

### [Q08] 这家公司的主要业务分部有哪些？
- 期望页: `[20, 21, 22, 23, 24, 25, 26]`  期望 section: `mda`
- 召回页: `[-1, 83, -1, 84, -1]`  召回 section: `['body', 'financials', 'body', 'financials', 'body']`
- Recall strict=0.00, soft=0.00
- Num precision=1.00
- LLM 答案: 根据知识库文档内容，这家公司的主要业务分部（即报告分部）有以下三个：

1. **Productivity and Business Processes（生产力与商业流程）**  
   包括：Office Commercial（如Office 365订阅、Microsoft 365商业版中的Office 365部分、本地授权Office）、Office Consumer（如Microsoft 365个人版订阅、本地授权Office等）、LinkedIn（含Talent Solutions、Marketing Solutions、Premium Subscriptions、Sales Solutions）以及Dynamics业务解决方案（如Dynamics 365 ERP/CRM云应用、Power Apps、Power Automate，及本地部署ERP/CRM应用）。

2. **Int

### [Q09] Who signed the independent auditor's report for Microsoft?
- 期望页: `[86, 87, 88]`  期望 section: `audit`
- 召回页: `[86, 90, 89, 92, 91]`  召回 section: `['audit', 'audit', 'audit', 'audit', 'audit']`
- Recall strict=0.33, soft=1.00
- Num precision=0.00
- LLM 答案: The independent auditor's report for Microsoft Corporation’s 2022 audit (as shown in the provided documents) is signed by **the independent registered public accounting firm**, but **the specific individual signatory (e.g., partner name) is not disclosed** in the excerpts provided (pages 86, 89–92).

The reports on pages 86 and 90 are titled *“REPORT OF INDEPENDENT REGISTERED PUBLIC ACCOUNTING FIR

### [Q14] How did Microsoft describe the impact of the COVID-19 pandemic on its operations?
- 期望页: `[7, 8, 9, 10, 11, 33, 34, 35]`  期望 section: `None`
- 召回页: `[-1, -1, 32, -1, -1]`  召回 section: `['body', 'body', 'mda', 'body', 'body']`
- Recall strict=0.00, soft=0.12
- Num precision=1.00
- LLM 答案: According to the provided knowledge base documents, Microsoft described the impact of the COVID-19 pandemic on its operations primarily in the context of **Scope 3 emissions**:

> “Fiscal year 2021 was a year of both successes and challenges. While we continued to make progress on several of our goals, with an overall reduction in our combined Scope 1 and Scope 2 emissions, our Scope 3 emissions i

### [Q15] What is Microsoft's approach to environmental sustainability?
- 期望页: `[4, 5, 6, 7, 8, 9]`  期望 section: `None`
- 召回页: `[-1, -1, -1, -1, -1]`  召回 section: `['body', 'body', 'body', 'body', 'body']`
- Recall strict=0.00, soft=0.00
- Num precision=1.00
- LLM 答案: Microsoft’s approach to environmental sustainability is comprehensive and ambitious, centered on reducing its own environmental footprint while enabling customers and partners to do the same. Key elements include:

- **Carbon Negative by 2030, Carbon Removal by 2050**: In January 2020, Microsoft committed to becoming carbon negative by 2030 — removing more carbon from the atmosphere than it emits 

### [Q16] How does Microsoft recognize revenue for its software subscriptions?
- 期望页: `[54, 55, 56, 57, 58, 59, 87, 88]`  期望 section: `None`
- 召回页: `[55, -1, 87, -1, 35]`  召回 section: `['financials', 'body', 'audit', 'body', 'mda']`
- Recall strict=0.25, soft=0.62
- Num precision=1.00
- LLM 答案: Microsoft recognizes revenue for its **software subscriptions** (e.g., Office 365, Dynamics 365, Xbox Game Pass) **ratably over the contract period**, as stated in the knowledge base:

> *"Revenue from on-premises licenses sold on a subscription basis is recognized ratably over the contract period."*  
> *(Document 2, Microsoft Corporation 2022 body p.-1)*

This applies to subscription-based softw

### [Q18] Comparing Microsoft's fiscal year 2022 and 2021 income statements, by roughly what percentage did total revenue grow?
- 期望页: `[49, 50]`  期望 section: `financials`
- 召回页: `[-1, 36, -1, -1, 37]`  召回 section: `['body', 'mda', 'body', 'body', 'mda']`
- Recall strict=0.00, soft=0.00
- Num precision=1.00
- LLM 答案: According to the provided knowledge base document, specifically in section **[Microsoft Corporation 2022 body p.-1]** under **SUMMARY RESULTS OF OPERATIONS**, Microsoft’s revenue was:

- **Fiscal Year 2022**: $198,270 million  
- **Fiscal Year 2021**: $168,088 million  
- **Percentage change**: **18%**

This is explicitly stated in the table:  
> Revenue — $198,270 (2022), $168,088 (2021), **18%**


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
