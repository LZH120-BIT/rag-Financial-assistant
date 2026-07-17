import json
import asyncio
from typing import Optional, List, Dict, AsyncGenerator
from bson import ObjectId
import redis.asyncio as aioredis

from openai import OpenAI
from app.config import settings
from app.chat.models import ChatData
from app.chat.prompts import med_assistant_data_prompt
from app.chat.tools import tools_data
from app.file.models import FileDocument
from app.file.service import file_service

controller_map: Dict[str, asyncio.Event] = {}


class ChatService:
    def __init__(self):
        self.openai = OpenAI(
            api_key=settings.TONGYI_AKI_KEY,
            base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        )

    async def query_file(self, user_id: str, doc_ids: List[str]):
        print(f"  │  [ChatService.query_file] 从 MongoDB 查询文档内容，docId列表: {json.dumps(doc_ids)}")
        objs = [ObjectId(did) for did in doc_ids]
        files = await FileDocument.find(
            {"userId": user_id, "_id": {"$in": objs}},
        ).to_list()
        print(f"  │  ✅ 查询到 {len(files)} 个文档，文件名: {', '.join(f.file_name for f in files)}")
        return {
            "uploadFileList": [
                {
                    "fileName": f.file_name,
                    "fileSize": f.file_size,
                    "fileType": f.file_type,
                    "docId": str(f.id),
                }
                for f in files
            ],
            "documents": [f.file_text for f in files],
        }

    async def calling_model(self, message_list: list, is_knowledge_based: Optional[bool] = None,
                            cancel_event: Optional[asyncio.Event] = None):
        print(f"  │  [ChatService.calling_model] 调用 qwen-plus")
        print(f"  │    isKnowledgeBased: {is_knowledge_based}")
        tool_choice = "none"
        system_content = med_assistant_data_prompt
        if is_knowledge_based:
            tool_choice = {"type": "function", "function": {"name": "H300"}}
            print(f"  │    tool_choice: 强制调用工具 H300（意图理解 + 实体抽取 + refuse 分类）")
            # 注入 KB 实体清单，帮助 LLM 把用户口语化实体对齐到规范全称（通用做法，不打表）
            try:
                kb = await file_service.get_shared_kb_entities()
                if kb["companies"] or kb["tickers"]:
                    entity_hint = (
                        "\n\n═══════════════════════════════════════════════\n"
                        "【KNOWLEDGE BASE ENTITY LIST — for intent tool only / 知识库实体清单，仅供 intent 工具参考】\n"
                        "═══════════════════════════════════════════════\n"
                        "When extracting entities.company / entities.ticker in the H300 tool call, "
                        "prefer aligning to the canonical names below (e.g. user says 'Microsoft' → output 'Microsoft Corporation').\n"
                        "抽取实体时优先对齐到下述规范全称。用户提到但清单里没有的实体，原样输出即可。\n\n"
                        f"Companies ({len(kb['companies'])}): {', '.join(kb['companies'])}\n\n"
                        f"Tickers ({len(kb['tickers'])}): {', '.join(kb['tickers'])}\n"
                    )
                    system_content = med_assistant_data_prompt + entity_hint
                    print(f"  │    注入 KB 实体清单: {len(kb['companies'])} companies / {len(kb['tickers'])} tickers")
            except Exception as e:
                print(f"  │    ⚠️ 注入 KB 实体清单失败 {e}，使用原始 system prompt")
        else:
            print(f"  │    tool_choice: none（不使用工具，直接回答）")
        print(f"  │    消息数量: {len(message_list)} 条（含system prompt）")

        return self.openai.chat.completions.create(
            model="qwen-plus",
            messages=[
                {"role": "system", "content": system_content},
                *message_list,
            ],
            stream=True,
            tools=tools_data,
            tool_choice=tool_choice,
        )

    async def combine_convo(self, user_id: str, session_id: str, content: str,
                            upload_file_list: Optional[list], stream, redis: aioredis.Redis,
                            is_knowledge_based: Optional[bool] = None):
        print("  ┌─ [ChatService.combine_convo] 开始组装对话上下文")

        message = {"role": "user", "content": content}
        read_file_list = None

        if upload_file_list and len(upload_file_list) > 0:
            is_knowledge_based = False
            print(f"  │  📎 [文档对话链路] 用户携带 {len(upload_file_list)} 个文档")
            print(f"  │  强制关闭知识库检索(isKnowledgeBased=false)")
            print(f"  │  → 推送\"正在阅读文档\"状态给前端")

            yield json.dumps({
                "type": "readDocument",
                "statusInfo": "inProgress",
                "promptInfo": "正在阅读文档",
                "fileList": [],
            }, ensure_ascii=False) + "###ABC###"

            doc_ids = [item["docId"] if isinstance(item, dict) else item.doc_id for item in upload_file_list]
            res = await self.query_file(user_id, doc_ids)
            document_content = "\n\n---\n\n".join(res["documents"])

            print(f"  │  ✅ 文档全文拼接完成，总字数: {len(document_content)}")
            print(f"  │  将文档内容拼入 Prompt")

            message["content"] = f"用户上传的文档内容如下:\n{document_content}\n请基于文档内容回复用户问题:{content}"
            message["displayContent"] = content
            message["uploadFileList"] = res["uploadFileList"]
            read_file_list = {
                "type": "readDocument",
                "statusInfo": "completed",
                "promptInfo": "文档阅读完毕",
                "fileList": [item["fileName"] for item in res["uploadFileList"]],
            }

        history_convo_list = []
        if session_id == "null":
            print(f"  │  🆕 新会话，无历史记录，直接以当前问题作为首条消息")
            history_convo_list.append(message)
        else:
            redis_key = f"chat_history:{user_id}:{session_id}"
            print(f"  │  🔍 查询历史记录，Redis Key: {redis_key}")
            cached = await redis.get(redis_key)
            if cached:
                history_convo_list = json.loads(cached)
                print(f"  │  ✅ 命中 Redis 缓存，历史消息共 {len(history_convo_list)} 条（无需查 MongoDB）")
            else:
                print(f"  │  ⚠️  Redis 未命中，从 MongoDB 查询历史记录...")
                chat_data = await ChatData.find_one(
                    ChatData.user_id == user_id,
                    ChatData.id == ObjectId(session_id),
                )
                if chat_data:
                    history_convo_list = chat_data.chat_list
                print(f"  │  ✅ MongoDB 查询完成，历史消息共 {len(history_convo_list)} 条")
                print(f"  │  → 写入 Redis 缓存（有效期3小时）")
                await redis.set(redis_key, json.dumps(history_convo_list, ensure_ascii=False), ex=10800)
            history_convo_list.append(message)
            print(f"  │  追加当前问题后，对话列表共 {len(history_convo_list)} 条")

        slice_count = min(len(history_convo_list), 21)
        print(f"  │  截取最近 {slice_count} 条对话传给模型（防止超出Token限制）")
        print(f"  └─ [ChatService.combine_convo] 上下文组装完毕，进入 modelResult()\n")

        async for chunk in self.model_result(
            history_convo_list[-21:],
            user_id,
            session_id,
            read_file_list,
            upload_file_list,
            is_knowledge_based,
            redis,
        ):
            yield chunk

    async def model_result(self, message_list: list, user_id: str, session_id: str,
                           read_file_list: Optional[dict], upload_file_list: Optional[list],
                           is_knowledge_based: Optional[bool], redis: aioredis.Redis):
        try:
            cancel_event = asyncio.Event()
            controller_key = f"{user_id}:{session_id}"
            controller_map[controller_key] = cancel_event

            print("  ┌─ [ChatService.modelResult] 开始调用模型，等待流式输出...")

            stream_response = await self.calling_model(message_list, is_knowledge_based, cancel_event)

            if upload_file_list and len(upload_file_list) > 0:
                print(f"  │  📎 [文档对话链路] 推送\"文档阅读完毕\"状态给前端")
                yield json.dumps(read_file_list, ensure_ascii=False) + "###ABC###"

            tool_call_args_str = ""
            is_tool_call_started = False
            assistant_message = ""

            print(f"  │  开始迭代模型流式输出 chunk...")

            for chunk in stream_response:
                if cancel_event.is_set():
                    break
                delta = chunk.choices[0].delta

                if delta.tool_calls and delta.tool_calls[0].function and delta.tool_calls[0].function.arguments:
                    tool_call_args_str += delta.tool_calls[0].function.arguments
                    if not is_tool_call_started:
                        print(f"  │  🔧 [RAG链路] 模型开始调用工具 H300（意图理解），正在拼接参数...")
                    is_tool_call_started = True

                if chunk.choices[0].finish_reason == "stop" and is_tool_call_started:
                    try:
                        new_question = json.loads(tool_call_args_str)
                    except json.JSONDecodeError:
                        new_question = {}

                    intent = new_question.get("intent", "qa")
                    entities = new_question.get("entities", {}) or {}
                    should_refuse = bool(new_question.get("should_refuse", False))
                    clarified = new_question.get("clarified_question", "").strip()

                    print(f"  │  🔧 [RAG链路] 工具输出:")
                    print(f"  │    intent={intent} should_refuse={should_refuse}")
                    print(f"  │    entities={json.dumps(entities, ensure_ascii=False)}")
                    if clarified:
                        print(f"  │    clarified_question=\"{clarified}\"")

                    # ▼ 通用短路：投资建议 / off-topic 直接拒答，不进检索也不进 LLM 生成
                    # 这是路由层的合规底线，prompt Rule 7 是第二道防线
                    if should_refuse:
                        # 检测用户问题主语言（简单规则：ASCII 占比 > 70% 视为英文）
                        raw_q = message_list[-1].get("displayContent") or message_list[-1].get("content", "")
                        is_english = raw_q and sum(1 for c in raw_q if ord(c) < 128) / max(len(raw_q), 1) > 0.7
                        if intent == "investment_advice":
                            refuse_msg = (
                                "I don't provide investment advice, price targets, or buy/sell recommendations. "
                                "For investment decisions, please consult a licensed financial advisor and the company's official disclosures."
                                if is_english else
                                "我不提供投资建议、目标价或买卖推荐。投资决策请咨询持牌财务顾问并查阅公司官方披露。"
                            )
                        else:  # off_topic
                            refuse_msg = (
                                "Sorry, I only handle financial-report and finance-related questions."
                                if is_english else
                                "抱歉，我只处理财报和金融相关的问题。"
                            )
                        print(f"  │  🚫 [路由短路] intent={intent}，直接返回 refuse 文案（不查库、不调二次 LLM）")
                        yield json.dumps({"role": "assistant", "content": refuse_msg}, ensure_ascii=False) + "###ABC###"
                        assistant_message = refuse_msg
                        continue

                    if clarified:
                        print(f"  │  🔧 [RAG链路] 使用改写后问题检索: \"{clarified}\"")
                        user_question = clarified
                    else:
                        user_question = message_list[-1]["content"]

                    rag_result: Dict = {"assistant_message": "", "read_file_list": None}
                    async for chunk_data in self.query_kb(
                        user_question, user_id, message_list, read_file_list, rag_result,
                        entities=entities,
                    ):
                        yield chunk_data
                    assistant_message = rag_result["assistant_message"]
                    read_file_list = rag_result["read_file_list"]

                if delta.content:
                    if assistant_message == "":
                        print(f"  │  💬 [普通/文档链路] 模型开始流式输出内容...")
                    yield json.dumps({"role": "assistant", "content": delta.content}, ensure_ascii=False) + "###ABC###"
                    assistant_message += delta.content or ""

            print(f"  │  ✅ 模型输出完毕，总回复字数: {len(assistant_message)}")
            print(f"  │")
            print(f"  │  ▶ 开始持久化对话记录...")

            assistant_message_obj = {
                "role": "assistant",
                "content": assistant_message,
            }
            if read_file_list:
                assistant_message_obj["readFileData"] = read_file_list

            convo_pair = [message_list[-1], assistant_message_obj]

            if session_id == "null":
                print(f"  │  🆕 新会话: 在 MongoDB 创建新的 ChatData 文档")
                new_chat = await ChatData(user_id=user_id, chat_list=convo_pair).insert()
                redis_key = f"chat_history:{user_id}:{new_chat.id}"
                await redis.set(redis_key, json.dumps(convo_pair, ensure_ascii=False), ex=10800)
                print(f"  │  ✅ 新会话创建完毕，sessionId={new_chat.id}")
                print(f"  │  → 同步写入 Redis 缓存，Key: {redis_key}")
                print(f"  │  → 推送 sessionId 给前端")
                yield json.dumps({
                    "role": "sessionId",
                    "content": str(new_chat.id),
                    "modelPrompt": "新会话已创建，请保存会话id",
                }, ensure_ascii=False) + "###ABC###"
            else:
                print(f"  │  ♻️  追加到已有会话: MongoDB $push chatList，sessionId={session_id}")
                await ChatData.find_one(
                    ChatData.user_id == user_id,
                    ChatData.id == ObjectId(session_id),
                ).update({"$push": {"chatList": {"$each": convo_pair}}})
                redis_key = f"chat_history:{user_id}:{session_id}"
                cached = await redis.get(redis_key)
                if cached:
                    parsed = json.loads(cached)
                    updated = parsed + convo_pair
                    await redis.set(redis_key, json.dumps(updated, ensure_ascii=False), ex=10800)
                    print(f"  │  ✅ MongoDB 更新完毕，Redis 缓存同步更新（当前共 {len(updated)} 条消息）")

            print(f"  └─ [ChatService.modelResult] 本次对话全部完成，关闭流\n")

        except Exception as e:
            print(f"  └─ [ChatService.modelResult] ❌ 出错: {e}")
            import traceback
            traceback.print_exc()
            yield json.dumps({"role": "error", "content": str(e), "modelPrompt": "模型回复出错"}, ensure_ascii=False) + "###ABC###"
        finally:
            controller_map.pop(controller_key, None)

    async def query_kb(self, user_question: str, user_id: str, message_list: list,
                       read_file_list: Optional[dict], result_holder: Optional[Dict] = None,
                       entities: Optional[Dict] = None):
        print(f"\n  ┌─ [ChatService.query_kb] RAG知识库检索流程开始")
        print(f"  │  检索问题: \"{user_question}\"")
        print(f"  │  实体提示: {json.dumps(entities or {}, ensure_ascii=False)}")
        print(f"  │  → 推送\"正在检索知识库\"状态给前端")

        yield json.dumps({
            "type": "queryKB",
            "statusInfo": "inProgress",
            "promptInfo": "正在检索知识库",
            "fileList": [],
        }, ensure_ascii=False) + "###ABC###"

        print(f"  │  ▶ 步骤1: 将用户问题转换为向量（调用 text-embedding-v2，query 端启用财报归一化）")
        # normalize=True：query 端和 doc 端做同样的归一化（千分位/单位/括号负数/日期），
        # 保证 "5,234 million" ≈ "$5.2 billion" ≈ "(5234)" 在向量空间对齐，命中率优化。
        vector_result = await file_service.embeddings_aliyun([user_question], normalize=True)
        print(f"  │  ✅ 步骤1完成: 问题已转为 1536 维向量（已归一化）")
        print(f"  │  ▶ 步骤2: 用向量在 Milvus 中混合搜索 + 关键词过滤")

        # ▼ 路由决策：优先走用户个人库；用户没上传就 fallback 到共享金融知识库（101 份 golden set）
        # 判断依据：pymilvus.utility.has_collection(f"_{user_id}")
        from pymilvus import utility
        personal_col = f"_{user_id}"
        has_personal = utility.has_collection(personal_col)
        print(f"  │    路由: 个人库[{personal_col}] 存在={has_personal}")

        if has_personal:
            print(f"  │    → 走个人库 search_database")
            search_results = await file_service.search_database(
                user_id, user_question, vector_result[0].embedding
            )
        else:
            print(f"  │    → 走共享金融库 search_shared_database (golden_100_reports)")
            # 从 intent tool 抽出的实体透传成 Milvus metadata filter
            ents = entities or {}
            search_results = await file_service.search_shared_database(
                user_question=user_question,
                question_vector=vector_result[0].embedding,
                companies=ents.get("company") or [],
                tickers=ents.get("ticker") or [],
                years=ents.get("year") or [],
                section=ents.get("section") or None,
                top_k=8,  # 共享库跨 101 份，稍微给多点
            )

        print(f"  │  ✅ 步骤2完成: 命中知识库文档 {len(search_results['searchDocTitle'])} 篇")
        print(f"  │  → 推送\"知识库检索完毕\"状态给前端")

        last_item = message_list[-1]
        last_item["displayContent"] = last_item["content"]
        last_item["content"] = search_results["searchDocText"]

        read_file_list = {
            "type": "queryKB",
            "statusInfo": "completed",
            "promptInfo": f"为你检索到{len(search_results['searchDocTitle'])}篇知识库",
            "fileList": search_results["searchDocTitle"],
        }
        yield json.dumps(read_file_list, ensure_ascii=False) + "###ABC###"

        print(f"  │  ▶ 步骤3: 将检索到的文档内容拼入 Prompt，二次调用 qwen-plus 生成回答")
        stream_response = await self.calling_model(message_list)
        assistant_message = ""
        print(f"  │  模型开始流式输出最终回答...")

        for chunk in stream_response:
            delta = chunk.choices[0].delta
            if delta.content:
                yield json.dumps({"role": "assistant", "content": delta.content}, ensure_ascii=False) + "###ABC###"
                assistant_message += delta.content or ""

        if result_holder is not None:
            result_holder["assistant_message"] = assistant_message
            result_holder["read_file_list"] = read_file_list

        print(f"  └─ [ChatService.query_kb] RAG检索+回答完毕，回复字数: {len(assistant_message)}\n")

    async def get_chat_list(self, user_id: str):
        print(f"  ┌─ [ChatService.get_chat_list] 聚合查询 MongoDB，userId={user_id}")
        pipeline = [
            {"$match": {"userId": user_id}},
            {
                "$project": {
                    "sessionId": "$_id",
                    "_id": 0,
                    "createTime": 1,
                    "chatList": {
                        "$map": {
                            "input": {"$slice": ["$chatList", 1]},
                            "as": "item",
                            "in": {"content": {"$ifNull": ["$$item.displayContent", "$$item.content"]}},
                        },
                    },
                },
            },
            {"$sort": {"createTime": -1}},
            {"$unwind": "$chatList"},
            {"$project": {"sessionId": 1, "content": "$chatList.content"}},
        ]
        collection = ChatData.get_pymongo_collection()
        cursor = collection.aggregate(pipeline)
        results = await cursor.to_list(length=None)
        formatted = []
        for r in results:
            sid = r.get("sessionId")
            if isinstance(sid, ObjectId):
                sid = str(sid)
            formatted.append({"sessionId": sid, "content": r.get("content", "")})
        print(f"  └─ [ChatService.get_chat_list] 返回 {len(formatted)} 条会话记录\n")
        return {"message": "SUCCESS", "result": formatted}

    async def single_chat_data(self, user_id: str, session_id: str, redis: aioredis.Redis):
        print(f"  ┌─ [ChatService.single_chat_data] 查询会话 {session_id}")
        redis_key = f"chat_history:{user_id}:{session_id}"
        print(f"  │  先查 Redis: {redis_key}")

        cached = await redis.get(redis_key)
        if cached:
            data = json.loads(cached)
            print(f"  │  ✅ 命中 Redis，消息共 {len(data)} 条")
            print(f"  └─ [ChatService.single_chat_data] 返回 {len(data)} 条消息\n")
            return {"message": "SUCCESS", "result": data}

        print(f"  │  ⚠️  Redis 未命中，查询 MongoDB...")
        chat_data = await ChatData.find_one(
            ChatData.user_id == user_id,
            ChatData.id == ObjectId(session_id),
        )
        if chat_data:
            data = chat_data.chat_list
            await redis.set(redis_key, json.dumps(data, ensure_ascii=False), ex=10800)
            print(f"  │  ✅ MongoDB 查询完毕，写入 Redis 缓存")
            print(f"  └─ [ChatService.single_chat_data] 返回 {len(data)} 条消息\n")
            return {"message": "SUCCESS", "result": data}
        print(f"  └─ [ChatService.single_chat_data] 未找到会话\n")
        return {"message": "SUCCESS", "result": []}

    def stop_output(self, user_id: str, session_id: str):
        controller_key = f"{user_id}:{session_id}"
        cancel_event = controller_map.get(controller_key)
        if cancel_event:
            print(f"  [ChatService.stop_output] 找到 cancel_event，中止 key={controller_key} 的模型请求")
            cancel_event.set()
            return {"message": "SUCCESS", "result": []}
        print(f"  [ChatService.stop_output] ⚠️  未找到 key={controller_key} 的控制器")
        return {"message": "会话没有找到，停止生成失败", "result": [], "code": 422}

    async def delete_chat(self, user_id: str, session_id: str, redis: aioredis.Redis):
        print(f"  ┌─ [ChatService.delete_chat] 删除会话 sessionId={session_id}")
        print(f"  │  步骤1: 从 MongoDB 删除（带 userId 条件，防止删别人的会话）")
        del_result = await ChatData.find(
            ChatData.user_id == user_id,
            ChatData.id == ObjectId(session_id),
        ).delete()
        if not del_result or del_result.deleted_count == 0:
            print(f"  └─ [ChatService.delete_chat] ⚠️  未找到该会话或无权限删除\n")
            return {"message": "会话不存在或无权限", "result": [], "code": 422}
        redis_key = f"chat_history:{user_id}:{session_id}"
        await redis.delete(redis_key)
        print(f"  │  步骤2: 已删除 Redis 缓存，Key: {redis_key}")
        print(f"  └─ [ChatService.delete_chat] ✅ 会话删除完毕\n")
        return {"message": "SUCCESS", "result": []}


chat_service = ChatService()