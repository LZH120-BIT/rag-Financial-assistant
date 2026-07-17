from fastapi import APIRouter, Depends, Request, Query
from fastapi.responses import StreamingResponse
from app.middleware.auth import get_current_user
from app.chat.schemas import SendMessageReq, SingleChatDataReq, DeleteChatReq
from app.chat.service import chat_service

router = APIRouter(prefix="/chat", tags=["chat"])


@router.post("/sendmessage")
async def send_message(
    request: Request,
    body: SendMessageReq,
    user_id: str = Depends(get_current_user),
):
    print("\n╔══════════════════════════════════════════════════════════╗")
    print("║  💬 [ChatController] 收到发送消息请求                        ║")
    print("╚══════════════════════════════════════════════════════════╝")
    print(f"  用户ID: {user_id}")
    print(f"  用户输入: \"{body.content}\"")
    print(f"  会话ID: {'新会话(null)' if body.session_id == 'null' else body.session_id}")
    print(f"  是否开启知识库: {'✅ 是 → 走RAG链路' if body.is_knowledge_based else '❌ 否'}")
    has_docs = body.upload_file_list and len(body.upload_file_list) > 0
    print(f"  是否携带文档: {'✅ 是，共' + str(len(body.upload_file_list)) + '个 → 走文档对话链路' if has_docs else '❌ 否 → 走普通对话链路'}")

    if not body.is_knowledge_based and not has_docs:
        print("\n  🛣️  判断链路: 【普通对话链路】")
        print("     流程: 用户问题 → 历史记录 → 大模型 → 流式返回")
    elif has_docs:
        print("\n  🛣️  判断链路: 【文档对话链路】")
        print("     流程: 读取文档全文 → 拼入Prompt → 大模型 → 流式返回")
    else:
        print("\n  🛣️  判断链路: 【RAG知识库链路】")
        print("     流程: 用户问题 → 工具调用(意图理解/改写) → 向量检索Milvus → 关键词过滤 → 大模型 → 流式返回")
    print("\n  ➡️  调用 ChatService.combine_convo() ...\n")

    redis = request.app.state.redis
    upload_list = None
    if body.upload_file_list:
        upload_list = [item.model_dump(by_alias=True) for item in body.upload_file_list]

    return StreamingResponse(
        chat_service.combine_convo(
            user_id, body.session_id, body.content,
            upload_list, None, redis, body.is_knowledge_based,
        ),
        media_type="text/plain",
    )


@router.get("/getchatlist")
async def get_chat_list(user_id: str = Depends(get_current_user)):
    print(f"\n  📋 [ChatController] 获取对话列表，userId={user_id}")
    return await chat_service.get_chat_list(user_id)


@router.get("/singlechatdata")
async def single_chat_data(
    request: Request,
    session_id: str = Query(alias="sessionId"),
    user_id: str = Depends(get_current_user),
):
    print(f"\n  📖 [ChatController] 获取会话详情，userId={user_id}，sessionId={session_id}")
    redis = request.app.state.redis
    return await chat_service.single_chat_data(user_id, session_id, redis)


@router.get("/stopoutput")
async def stop_output(
    session_id: str = Query(alias="sessionId"),
    user_id: str = Depends(get_current_user),
):
    print(f"\n  🛑 [ChatController] 终止模型输出，userId={user_id}，sessionId={session_id}")
    return chat_service.stop_output(user_id, session_id)


@router.post("/deletechat")
async def delete_chat(
    request: Request,
    body: DeleteChatReq,
    user_id: str = Depends(get_current_user),
):
    print(f"\n  🗑️  [ChatController] 删除会话，userId={user_id}，sessionId={body.session_id}")
    redis = request.app.state.redis
    return await chat_service.delete_chat(user_id, body.session_id, redis)