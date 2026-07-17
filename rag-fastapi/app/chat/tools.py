from app.chat.prompts import intent_understanding_prompt

tools_data = [
    {
        "type": "function",
        "function": {
            "name": "H300",
            "description": intent_understanding_prompt,
            "parameters": {
                "type": "object",
                "properties": {
                    "intent": {
                        "type": "string",
                        "enum": ["qa", "investment_advice", "off_topic", "greeting", "enumeration", "comparison"],
                        "description": "用户问题的意图类别。qa=常规财报事实问答；investment_advice=询问买卖持有/目标价/评级（合规拒答）；off_topic=与金融无关；greeting=打招呼；enumeration=让列举库里所有满足条件的公司；comparison=对比多家公司。",
                    },
                    "entities": {
                        "type": "object",
                        "description": "从用户问题中抽取的结构化实体，用于 Milvus metadata 过滤。只抽出用户明确提到或明确暗示的实体，不要臆测。",
                        "properties": {
                            "company": {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": "公司名列表。尽量匹配到知识库中的规范全称（如 'Microsoft' → 'Microsoft Corporation'）；无法匹配则原样输出。",
                            },
                            "ticker": {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": "股票代码列表，大写。如 ['MSFT', 'AAPL']。",
                            },
                            "year": {
                                "type": "array",
                                "items": {"type": "integer"},
                                "description": "财年列表，整数。如 [2022, 2023]。",
                            },
                            "section": {
                                "type": "string",
                                "enum": ["risk", "mda", "financials", "audit", "appendix", "body", "toc"],
                                "description": "章节类型。仅当用户明确指向某章节时填写：risk=风险因素/Risk Factors；mda=MD&A；financials=财务报表；audit=审计报告。",
                            },
                            "metric": {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": "财务指标列表，如 ['Revenue'], ['R&D expense'], ['gross margin']。",
                            },
                        },
                    },
                    "should_refuse": {
                        "type": "boolean",
                        "description": "是否应该硬拒答。intent 为 investment_advice 或 off_topic 时必须为 true；其他情况为 false。",
                    },
                    "clarified_question": {
                        "type": "string",
                        "description": "如果用户问题是续问（需要结合上下文才完整），生成完整的新问题；否则留空。",
                    },
                },
                "required": ["intent", "should_refuse"],
            },
        },
    },
]
