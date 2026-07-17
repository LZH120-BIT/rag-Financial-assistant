import os
import uuid
from typing import List
from fastapi import APIRouter, Depends, UploadFile, File, Form, Request
from app.middleware.auth import get_current_user
from app.file.service import file_service
from app.file.schemas import DeleteFileReq, UploadWebReq

router = APIRouter(prefix="/fileanagement", tags=["file"])

ALLOWED_MIMETYPES = {
    "application/pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}
ALLOWED_REPORT_MIMETYPES = {
    # 图片
    "image/jpeg", "image/jpg", "image/png", "image/webp", "image/bmp", "image/tiff",
    # PDF
    "application/pdf",
    # Office
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",   # docx
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",         # xlsx
}
# 按扩展名兜底（浏览器对 docx/xlsx 的 mimetype 有时上报为 application/octet-stream）
ALLOWED_REPORT_EXTENSIONS = {
    ".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tiff", ".tif",
    ".pdf", ".docx", ".xlsx",
}
MAX_REPORT_SIZE = 100 * 1024 * 1024  # 100MB (Golden Set 最大 Poste Italiane 62MB)


def _save_upload(file: UploadFile) -> str:
    ext = os.path.splitext(file.filename or "file")[1]
    unique_name = f"{uuid.uuid4().hex}{ext}"
    save_path = os.path.join("uploads", unique_name)
    os.makedirs("uploads", exist_ok=True)
    with open(save_path, "wb") as f:
        f.write(file.file.read())
    return save_path


@router.post("/uploadkb")
async def upload_kb(
    request: Request,
    file: List[UploadFile] = File(...),
    user_id: str = Depends(get_current_user),
):
    print("\n╔══════════════════════════════════════════════╗")
    print("║  📚 [知识库上传] 链路开始                        ║")
    print("╚══════════════════════════════════════════════╝")
    print(f"  用户ID: {user_id}")
    print(f"  上传文件数量: {len(file)} 个")

    if len(file) > 3:
        return {"message": "每次最多上传3个文件", "result": [], "code": 422}

    for f in file:
        if f.content_type not in ALLOWED_MIMETYPES:
            return {"message": "只能上传 PDF 或 DOCX 文件", "result": [], "code": 422}
        if f.size and f.size > MAX_REPORT_SIZE:
            return {"message": f"文件大小不能超过 {MAX_REPORT_SIZE // (1024*1024)}MB", "result": [], "code": 422}

    doc_ids: List[str] = []
    for f in file:
        save_path = _save_upload(f)
        print(f"\n  ┌─ 开始处理文件: 【{f.filename}】")
        print(f"  │  文件类型: {f.content_type}")
        print(f"  │  文件大小: {(os.path.getsize(save_path) / 1024):.2f} KB")
        print(f"  │  服务器存储路径: {save_path}")

        print(f"  │")
        print(f"  │  ▶ 步骤1: 读取文档内容 + 按800字/块拆分 (uploadType=UB)")
        result = await file_service.read_file(save_path, f.filename or "file", f.content_type or "", "UB")
        print(f"  │  ✅ 步骤1完成: 文档全文共 {len(result['mergeTexts'])} 字，拆分为 {len(result['splitDocument'])} 个块")

        print(f"  │")
        print(f"  │  ▶ 步骤2: 将文件元数据 + 全文存入 MongoDB")
        doc_id = await file_service.upload_file(
            save_path, f.filename or "file", f.content_type or "",
            os.path.getsize(save_path), user_id, result["mergeTexts"], "UB"
        )
        print(f"  │  ✅ 步骤2完成: MongoDB 文档ID = {doc_id}")

        print(f"  │")
        print(f"  │  ▶ 步骤3: 向量化文档块 + 写入 Milvus 向量数据库")
        print(f"  │          (每批最多25块，调用阿里云 text-embedding-v2 模型)")
        original_name = await file_service.vector_storage(
            f.filename or "file", result["splitDocument"], user_id, doc_id
        )
        print(f"  │  ✅ 步骤3完成: 向量化并写入 Milvus 成功 → {original_name}")
        print(f"  └─ 文件 【{f.filename}】 处理完毕，docId={doc_id}")

        doc_ids.append(doc_id)

    print("\n╔══════════════════════════════════════════════╗")
    print("║  ✅ [知识库上传] 全部文件处理完毕                  ║")
    print("╚══════════════════════════════════════════════╝")
    print(f"  返回 documentId 列表给前端: {doc_ids}\n")
    return {"message": "SUCCESS", "result": doc_ids}


@router.post("/uploaddialog")
async def upload_dialog(
    request: Request,
    file: List[UploadFile] = File(...),
    user_id: str = Depends(get_current_user),
):
    print("\n╔══════════════════════════════════════════════╗")
    print("║  📎 [对话框文件上传] 链路开始                      ║")
    print("╚══════════════════════════════════════════════╝")
    print(f"  用户ID: {user_id}")
    print(f"  上传文件数量: {len(file)} 个")
    print("  ⚠️  注意: 对话框上传的文件不拆分、不向量化，只存全文到 MongoDB")

    if len(file) > 3:
        return {"message": "每次最多上传3个文件", "result": [], "code": 422}

    for f in file:
        if f.content_type not in ALLOWED_MIMETYPES:
            return {"message": "只能上传 PDF 或 DOCX 文件", "result": [], "code": 422}
        if f.size and f.size > MAX_REPORT_SIZE:
            return {"message": f"文件大小不能超过 {MAX_REPORT_SIZE // (1024*1024)}MB", "result": [], "code": 422}

    doc_ids: List[str] = []
    for f in file:
        save_path = _save_upload(f)
        print(f"\n  ┌─ 处理文件: 【{f.filename}】")
        print(f"  │  ▶ 步骤1: 读取文档全文（不拆分）")
        result = await file_service.read_file(save_path, f.filename or "file", f.content_type or "", "UB")
        print(f"  │  ✅ 步骤1完成: 读取全文共 {len(result['mergeTexts'])} 字")
        print(f"  │  ▶ 步骤2: 将文件元数据 + 全文存入 MongoDB (uploadType=UD)")
        doc_id = await file_service.upload_file(
            save_path, f.filename or "file", f.content_type or "",
            os.path.getsize(save_path), user_id, result["mergeTexts"], "UD"
        )
        print(f"  │  ✅ 步骤2完成: MongoDB 文档ID = {doc_id}")
        print(f"  └─ 文件处理完毕，docId={doc_id}（后续对话可携带此ID）")
        doc_ids.append(doc_id)

    print("\n╔══════════════════════════════════════════════╗")
    print("║  ✅ [对话框文件上传] 全部完毕                      ║")
    print("╚══════════════════════════════════════════════╝")
    print(f"  返回 documentId 列表给前端: {doc_ids}\n")
    return {"message": "SUCCESS", "result": doc_ids}


@router.post("/deletefilekb")
async def delete_file_kb(body: DeleteFileReq, user_id: str = Depends(get_current_user)):
    print(f"\n  🗑️  [知识库删除] 用户ID={user_id} 删除 docId={body.doc_id}")
    print("  同时删除: MongoDB记录 + 服务器文件 + Milvus向量数据")
    return await file_service.delete_file_kb(user_id, body.doc_id)


@router.post("/deletefile")
async def delete_file(body: DeleteFileReq, user_id: str = Depends(get_current_user)):
    print(f"\n  🗑️  [对话框文件删除] 用户ID={user_id} 删除 docId={body.doc_id}")
    print("  删除: MongoDB记录 + 服务器文件（不涉及Milvus）")
    return await file_service.delete_file(user_id, body.doc_id)


@router.get("/kbfilelist")
async def kb_file_list(user_id: str = Depends(get_current_user)):
    return await file_service.get_kb_file_list(user_id)


@router.post("/uploadweb")
async def upload_web(body: UploadWebReq, user_id: str = Depends(get_current_user)):
    print("\n╔══════════════════════════════════════════════╗")
    print("║  🌐 [网页解析入知识库] 链路开始                    ║")
    print("╚══════════════════════════════════════════════╝")
    print(f"  用户ID: {user_id}")
    print(f"  网页URL: {body.url}")

    parsed = await file_service.parse_web_page(body.url)
    doc_id = await file_service.save_web_to_knowledge(user_id, body.url, parsed["title"], parsed["textContent"])

    print(f"  ✅ [网页解析入知识库] 完成，标题={parsed['title']}，docId={doc_id}")
    print("╚══════════════════════════════════════════════╝\n")
    return {"message": "SUCCESS", "result": {"docId": doc_id, "fileName": parsed["title"]}}


@router.post("/uploadreport")
async def upload_report(
    request: Request,
    file: UploadFile = File(...),
    user_id: str = Depends(get_current_user),
):
    print("\n╔══════════════════════════════════════════════╗")
    print("║  📊 [财报快速解读] 链路开始                        ║")
    print("╚══════════════════════════════════════════════╝")
    print(f"  用户ID: {user_id}")
    print(f"  文件名: {file.filename}")
    print(f"  文件类型(浏览器上报): {file.content_type}")

    # 扩展名兜底（浏览器对 docx/xlsx 经常上报为 application/octet-stream）
    ext = os.path.splitext(file.filename or "")[1].lower()
    ext_to_mt = {
        ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
        ".png": "image/png", ".webp": "image/webp",
        ".bmp": "image/bmp", ".tiff": "image/tiff", ".tif": "image/tiff",
        ".pdf": "application/pdf",
        ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    }
    if file.content_type in ALLOWED_REPORT_MIMETYPES:
        effective_mt = file.content_type
    elif ext in ext_to_mt:
        effective_mt = ext_to_mt[ext]
        print(f"  ⚙️  mimetype 不在白名单，按扩展名 {ext} 兜底为 {effective_mt}")
    else:
        return {
            "message": "财报文档只支持：JPG/PNG/WEBP/BMP/TIFF 图片，PDF/DOCX/XLSX 文档",
            "result": [], "code": 422,
        }

    if file.size and file.size > MAX_REPORT_SIZE:
        return {"message": f"文件大小不能超过 {MAX_REPORT_SIZE // (1024 * 1024)}MB", "result": [], "code": 422}

    save_path = _save_upload(file)

    result = await file_service.recognize_report(save_path, file.filename or "file", effective_mt)
    if not result["isValid"]:
        print(f"  ⚠️  [财报快速解读] 无法识别，返回提示\n")
        return {"message": "SUCCESS", "result": {"reportText": result["reportText"], "docId": None}}

    doc_id = await file_service.save_report_doc(
        save_path, file.filename or "file", effective_mt,
        os.path.getsize(save_path), user_id, result["reportText"]
    )

    # file_type 给前端用（选图标 / 显示标签）
    if effective_mt == "application/pdf":
        file_type = "PDF"
    elif effective_mt.startswith("image/"):
        file_type = "IMG"
    elif effective_mt == "application/vnd.openxmlformats-officedocument.wordprocessingml.document":
        file_type = "DOCX"
    elif effective_mt == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet":
        file_type = "XLSX"
    else:
        file_type = "FILE"
    file_size = f"{(os.path.getsize(save_path) / 1024):.2f}kb"

    redis = request.app.state.redis
    session_id = await file_service.create_report_session(
        user_id, file.filename or "file", file_type, file_size,
        str(doc_id), result["reportText"], redis,
    )

    print(f"  ✅ [财报快速解读] 完成，docId={doc_id}，sessionId={session_id}")
    print("╚══════════════════════════════════════════════╝\n")
    return {
        "message": "SUCCESS",
        "result": {
            "reportText": result["reportText"],
            "docId": doc_id,
            "sessionId": session_id,
            "fileName": file.filename,
            "fileType": file_type,
            "fileSize": file_size,
        },
    }