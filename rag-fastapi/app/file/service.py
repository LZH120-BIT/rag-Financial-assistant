import os
import json
import asyncio
from typing import List, Set
from bson import ObjectId
import redis.asyncio as aioredis

from langchain_community.document_loaders import PyPDFLoader, Docx2txtLoader
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_core.documents import Document as LangchainDocument
import trafilatura

from pymilvus import (
    Collection,
    CollectionSchema,
    FieldSchema,
    DataType,
    AnnSearchRequest,
    WeightedRanker,
    connections,
    utility,
)

from openai import OpenAI
from app.config import settings
from app.file.models import FileDocument


class FileService:
    def __init__(self):
        self.openai = OpenAI(
            api_key=settings.TONGYI_AKI_KEY,
            base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        )
        self._collections_loaded: Set[str] = set()
        # KB 实体清单缓存：{"companies": [...], "tickers": [...]}
        # 用途：喂给 intent_understanding LLM 让它把用户口语化实体对齐到规范全称
        # 缓存 lazy 加载；建议启动时/首次共享库检索时预热
        self._kb_entity_cache: dict = None

    async def get_shared_kb_entities(self, collection_name: str = None, force_refresh: bool = False) -> dict:
        """返回共享库里 distinct 的 company/ticker 清单。
        通用原则：不打表，每次直接从 Milvus 拉全量 metadata；结果缓存到内存。
        force_refresh=True 时忽略缓存重拉（用于入库新公司后刷新）。
        """
        if self._kb_entity_cache is not None and not force_refresh:
            return self._kb_entity_cache

        name = collection_name or self.SHARED_KB_COLLECTION
        try:
            self._ensure_collection_loaded(name)
            col = self._get_collection(name)
            # Milvus 没有 distinct，用 query + 手动去重；query_iterator 分页避免大集合 OOM
            it = col.query_iterator(
                expr="id > 0",
                output_fields=["company", "ticker"],
                batch_size=1000,
            )
            companies, tickers = set(), set()
            while True:
                batch = it.next()
                if not batch:
                    break
                for row in batch:
                    c = (row.get("company") or "").strip()
                    t = (row.get("ticker") or "").strip()
                    if c:
                        companies.add(c)
                    if t:
                        tickers.add(t.upper())
            it.close()
            self._kb_entity_cache = {
                "companies": sorted(companies),
                "tickers": sorted(tickers),
            }
            print(f"  │  [get_shared_kb_entities] 缓存 KB 实体：{len(companies)} companies / {len(tickers)} tickers")
        except Exception as e:
            print(f"  │  ⚠️ [get_shared_kb_entities] 加载失败 {e}，返回空清单")
            self._kb_entity_cache = {"companies": [], "tickers": []}
        return self._kb_entity_cache

    def _get_collection(self, collection_name: str) -> Collection:
        return Collection(name=collection_name)

    def _ensure_collection_loaded(self, collection_name: str):
        if collection_name not in self._collections_loaded:
            self._get_collection(collection_name).load()
            self._collections_loaded.add(collection_name)

    def _release_collection(self, collection_name: str):
        if collection_name in self._collections_loaded:
            self._get_collection(collection_name).release()
            self._collections_loaded.discard(collection_name)

    async def read_file(self, file_path: str, file_name: str, mimetype: str, upload_type: str):
        """财报 KB 上传统一走自研 OCR 管线（v3：table+column+header/footer+image+section）。
        chunk_size=1500 / overlap=200：财报表格/段落较长，1500 能容一个完整财报小表 + 描述。
        chunk 分隔符里加入 [section= 和 表格 |，尽量不把 markdown 表格切在中间。
        """
        file_type = "PDF" if mimetype == "application/pdf" else "DOCX"
        print(f"  │    [FileService.read_file] 文件类型: {file_type}, uploadType: {upload_type}")

        # 全部走自研 OCR 管线（PyPDFLoader 不带 section markers 和表格恢复）
        from app.file.ocr import recognize as ocr_recognize
        merged_text = await ocr_recognize(file_path, mimetype)
        print(f"  │    [FileService.read_file] OCR/解析后总字数: {len(merged_text)}")

        split_document: List[LangchainDocument] = []
        if upload_type == "UB":
            print(f"  │    [FileService.read_file] 开始文本拆分: chunkSize=1500, chunkOverlap=200")
            text_splitter = RecursiveCharacterTextSplitter(
                chunk_size=1500,
                chunk_overlap=200,
                # separators 顺序：优先按分区/页边界切；其次按段落；最后才回落到字符
                separators=[
                    "\n---\n",       # 页分隔（ocr.py 用 "---" 分页）
                    "\n[section=",   # section marker 边界（保留新块起始处的 marker）
                    "\n\n",
                    "\n",
                    "。", "！", "？", "；", "，", " ", "",
                ],
            )
            split_document = await text_splitter.atransform_documents(
                [LangchainDocument(page_content=merged_text)]
            )
            print(f"  │    [FileService.read_file] 拆分完成，共 {len(split_document)} 个文本块")

        return {"mergeTexts": merged_text, "splitDocument": split_document}

    async def upload_file(self, file_path: str, file_name: str, mimetype: str, file_size: int,
                          user_id: str, file_text: str, upload_type: str) -> str:
        file_type = "PDF" if mimetype == "application/pdf" else "DOCX"
        size_kb = f"{(file_size / 1024):.2f}kb"
        print(f"  │    [FileService.upload_file] 写入 MongoDB: fileName={file_name}, uploadType={upload_type}")

        doc = await FileDocument(
            user_id=user_id,
            file_name=file_name,
            file_path=file_path,
            file_type=file_type,
            file_size=size_kb,
            file_text=file_text,
            upload_type=upload_type,
        ).insert()
        return str(doc.id)

    async def create_collection(self, collection_name: str):
        print(f"  │    [FileService.create_collection] 创建 Milvus 集合: {collection_name}")
        print(f"  │    字段: id(主键), docId, docTitle, docText, embedDocTitle(1536维), embedDocText(1536维)")
        print(f"  │    索引: AUTOINDEX + COSINE余弦相似度")

        fields = [
            FieldSchema(name="id", dtype=DataType.INT64, is_primary=True, auto_id=True),
            FieldSchema(name="docId", dtype=DataType.VARCHAR, max_length=100),
            FieldSchema(name="docTitle", dtype=DataType.VARCHAR, max_length=500),
            FieldSchema(name="docText", dtype=DataType.VARCHAR, max_length=9000),
            FieldSchema(name="embedDocTitle", dtype=DataType.FLOAT_VECTOR, dim=1536),
            FieldSchema(name="embedDocText", dtype=DataType.FLOAT_VECTOR, dim=1536),
        ]
        schema = CollectionSchema(fields=fields)
        Collection(name=collection_name, schema=schema)

        collection = self._get_collection(collection_name)
        index_params = [
            {"field_name": "id", "index_type": "AUTOINDEX"},
            {"field_name": "docId", "index_type": "AUTOINDEX"},
            {"field_name": "docTitle", "index_type": "AUTOINDEX"},
            {"field_name": "embedDocTitle", "index_type": "AUTOINDEX", "metric_type": "COSINE"},
            {"field_name": "embedDocText", "index_type": "AUTOINDEX", "metric_type": "COSINE"},
        ]
        for idx in index_params:
            collection.create_index(**idx)
        collection.release()
        print(f"  │    [FileService.create_collection] ✅ 集合创建完毕并已释放")

    async def embeddings_aliyun(self, texts: List[str], normalize: bool = False) -> list:
        """调用 text-embedding-v2；normalize=True 时用财报归一化文本 embed（检索命中率优化）。
        docText 存储始终保留原文，只有 embedding 用归一化文本。
        """
        if normalize:
            from app.file.text_normalizer import normalize_for_retrieval
            embed_input = [normalize_for_retrieval(t) for t in texts]
        else:
            embed_input = texts
        print(f"  │    [FileService.embeddings_aliyun] 调用 text-embedding-v2，输入 {len(embed_input)} 条文本，维度=1536, normalize={normalize}")
        completion = self.openai.embeddings.create(
            model="text-embedding-v2",
            input=embed_input,
            dimensions=1536,
        )
        print(f"  │    [FileService.embeddings_aliyun] ✅ 向量生成完毕，返回 {len(completion.data)} 条向量")
        return completion.data

    async def insert_data(self, collection_name: str, original_name: str, doc_id: str,
                          data: List[LangchainDocument], vectors_doc_title: list, vectors_doc_text: list):
        entities = []
        for index, item in enumerate(data):
            entities.append({
                "docId": doc_id,
                "docTitle": original_name,
                "docText": item.page_content,
                "embedDocTitle": vectors_doc_title[0].embedding,
                "embedDocText": vectors_doc_text[index].embedding,
            })
        print(f"  │    [FileService.insert_data] 向 Milvus 集合 [{collection_name}] 插入 {len(entities)} 条向量数据")

        self._ensure_collection_loaded(collection_name)
        collection = self._get_collection(collection_name)

        max_retry = 5
        for attempt in range(1, max_retry + 1):
            try:
                res = collection.insert(entities)
                if res.insert_count == len(entities):
                    print(f"  │    [FileService.insert_data] ✅ 插入成功")
                    return "插入数据成功"
                if attempt < max_retry:
                    print(f"  │    [FileService.insert_data] ⏳ 插入不完整，第{attempt}次重试，等待{attempt}秒...")
                    await asyncio.sleep(attempt)
                    continue
                raise Exception(f"插入向量数据库失败: insert_count={res.insert_count}")
            except Exception as e:
                if attempt < max_retry:
                    print(f"  │    [FileService.insert_data] ⏳ 插入异常，第{attempt}次重试: {e}")
                    await asyncio.sleep(attempt)
                    continue
                print(f"  │    [FileService.insert_data] ❌ 重试{max_retry}次仍失败: {e}")
                raise

    async def vector_storage(self, file_name: str, split_document: List[LangchainDocument],
                             user_id: str, doc_id: str):
        collection_name = f"_{user_id}"
        print(f"  │    [FileService.vector_storage] Milvus 集合名: {collection_name}")

        if not utility.has_collection(collection_name):
            print(f"  │    [FileService.vector_storage] 集合不存在，开始创建...")
            await self.create_collection(collection_name)
        else:
            print(f"  │    [FileService.vector_storage] 集合已存在，跳过创建")

        print(f"  │    [FileService.vector_storage] ▶ 生成文档标题向量")
        vectors_doc_title = await self.embeddings_aliyun([file_name], normalize=True)

        batch_size = 25
        total_batches = (len(split_document) + batch_size - 1) // batch_size
        print(f"  │    [FileService.vector_storage] ▶ 分批向量化文档块: 共 {len(split_document)} 块，每批 {batch_size} 块，共 {total_batches} 批")

        for i in range(0, len(split_document), batch_size):
            batch = split_document[i:i + batch_size]
            batch_num = i // batch_size + 1
            print(f"  │    [FileService.vector_storage] 处理第 {batch_num}/{total_batches} 批，本批 {len(batch)} 块")
            vectors_doc_text = await self.embeddings_aliyun([item.page_content for item in batch], normalize=True)
            await self.insert_data(collection_name, file_name, doc_id, batch, vectors_doc_title, vectors_doc_text)

        return file_name

    async def search_database(self, user_id: str, user_question: str, question_vector: list):
        collection_name = f"_{user_id}"
        print(f"\n  ┌─ [FileService.search_database] RAG向量检索开始")
        print(f"  │  用户问题: \"{user_question}\"")
        print(f"  │  Milvus 集合: {collection_name}")
        print(f"  │  混合搜索策略:")
        print(f"  │    - 搜索1: 按文档标题向量(embedDocTitle) 检索 Top9")
        print(f"  │    - 搜索2: 按文档内容向量(embedDocText) 检索 Top9")
        print(f"  │    - 重排: WeightedRanker(标题权重=0.3, 内容权重=0.8)，取 Top18")

        self._ensure_collection_loaded(collection_name)
        collection = self._get_collection(collection_name)

        search_param_1 = AnnSearchRequest(
            data=[question_vector],
            anns_field="embedDocTitle",
            param={"metric_type": "COSINE"},
            limit=9,
        )
        search_param_2 = AnnSearchRequest(
            data=[question_vector],
            anns_field="embedDocText",
            param={"metric_type": "COSINE"},
            limit=9,
        )

        results = collection.hybrid_search(
            reqs=[search_param_1, search_param_2],
            rerank=WeightedRanker(0.3, 0.8),
            limit=18,
            output_fields=["docTitle", "docText"],
        )

        print(f"  │  ✅ Milvus 向量检索完毕，原始结果 {len(results[0])} 条")
        print(f"  │")
        print(f"  │  ▶ 提取关键词（用于二次过滤）...")

        keyword_list = await self.extract_keywords(user_question)
        print(f"  │  ✅ 关键词提取完毕: {json.dumps(keyword_list['keyWord'])}")
        print(f"  │")
        print(f"  │  ▶ 用关键词过滤检索结果（命中标题或正文含关键词的块保留）...")

        if results and len(results[0]) > 0:
            hits = results[0]
            filter_doc_list = self.filter_docs_by_keywords(hits, keyword_list["keyWord"])

            print(f"  │  ✅ 关键词过滤后保留 {len(filter_doc_list)} 条（原 {len(hits)} 条）")

            search_doc_title: List[str] = []
            search_doc_text = ""
            if len(filter_doc_list) > 0:
                search_doc_title = list(set(item.entity.get("docTitle", "") for item in filter_doc_list))
                for idx, item in enumerate(filter_doc_list):
                    search_doc_text += str(idx + 1) + "." + item.entity.get("docText", "")
                print(f"  │  命中文档: {json.dumps(search_doc_title)}")
            else:
                search_doc_text = "&没有检索到相关文档&"
                print(f"  │  ⚠️  关键词过滤后无匹配，将告知模型\"没有检索到相关文档\"")

            print(f"  └─ [FileService.search_database] RAG检索完毕，命中文档数={len(search_doc_title)}\n")
            return {
                "searchDocTitle": search_doc_title,
                "searchDocText": f"请根据检索到的知识库文档内容回复用户问题,用户问题:{user_question};\n文档内容:{search_doc_text}",
            }
        else:
            print(f"  └─ [FileService.search_database] ⚠️  Milvus 未检索到结果，返回空\n")
            return {
                "searchDocTitle": [],
                "searchDocText": f"请根据检索到的知识库文档内容回复用户问题,用户问题:{user_question};\n文档内容:&没有检索到相关文档&",
            }

    async def extract_keywords(self, content: str):
        from app.chat.prompts import kw_extraction_prompt
        print(f"  │    [FileService.extract_keywords] 调用 qwen-plus 提取关键词，输入: \"{content}\"")
        res = self.openai.chat.completions.create(
            model="qwen-plus",
            messages=[
                {"role": "system", "content": kw_extraction_prompt},
                {"role": "user", "content": content},
            ],
            stream=False,
            response_format={"type": "json_object"},
        )
        result = json.loads(res.choices[0].message.content)
        print(f"  │    [FileService.extract_keywords] ✅ 关键词: {json.dumps(result['keyWord'])}")
        return result

    def filter_docs_by_keywords(self, doc_list, key_words: List[str]) -> list:
        """
        关键词过滤（doc/keyword 双端归一化 + 大小写不敏感）。
        - LLM 抽出的关键词可能是 "FY2022 / Revenue / 5.2 billion" 等；
          doc 里的原文可能是 "fiscal 2022 / revenue / $5,234 million"。
        - 双端跑 normalize_for_retrieval 后再比对，能提升召回。
        - 若归一化后仍匹配失败，退化到原字符串大小写不敏感包含（兜底，避免 recall 下降）。
        """
        from app.file.text_normalizer import normalize_for_retrieval
        norm_kws = [(kw, normalize_for_retrieval(kw).lower()) for kw in key_words if kw]
        result = []
        for doc in doc_list:
            entity = doc.entity
            raw_combined = f"{entity.get('docTitle', '')}{entity.get('docText', '')}"
            norm_combined = normalize_for_retrieval(raw_combined).lower()
            raw_lower = raw_combined.lower()
            if any(nkw in norm_combined or kw.lower() in raw_lower for kw, nkw in norm_kws):
                result.append(doc)
        return result

    async def delete_file(self, user_id: str, doc_id: str):
        print(f"  │  [FileService.delete_file] 从 MongoDB 查询文件路径，然后删除记录 + 服务器文件")
        file_record = await FileDocument.find_one(FileDocument.id == ObjectId(doc_id), FileDocument.user_id == user_id)
        if file_record:
            await file_record.delete()
            file_path = file_record.file_path
            if file_path and not file_path.startswith("WEB::"):
                full_path = os.path.join(os.getcwd(), file_path)
                try:
                    os.unlink(full_path)
                    print(f"  │  ✅ 服务器文件已删除: {full_path}")
                except Exception as e:
                    print(f"  │  ⚠️  服务器文件删除失败: {full_path}")
            else:
                print(f"  │  ✅ 网页类型记录，无服务器文件需删除")
        return {"message": "SUCCESS", "result": []}

    async def delete_file_kb(self, user_id: str, doc_id: str):
        print(f"\n  ┌─ [FileService.delete_file_kb] 开始删除知识库文件 docId={doc_id}")
        await self.delete_file(user_id, doc_id)
        collection_name = f"_{user_id}"
        print(f"  │  加载 Milvus 集合，删除 docId={doc_id} 的向量数据")
        self._ensure_collection_loaded(collection_name)
        collection = self._get_collection(collection_name)
        collection.delete(f"docId == '{doc_id}'")
        print(f"  └─ [FileService.delete_file_kb] ✅ 删除完毕\n")
        return {"message": "SUCCESS", "result": []}

    async def get_kb_file_list(self, user_id: str):
        print(f"\n  📋 [知识库列表] 查询用户 {user_id} 的知识库文件列表")
        docs = await FileDocument.find(
            FileDocument.user_id == user_id,
            FileDocument.upload_type == "UB",
        ).to_list()
        result = [
            {
                "docId": str(doc.id),
                "fileName": doc.file_name,
                "fileType": doc.file_type,
                "fileSize": doc.file_size,
            }
            for doc in docs
        ]
        print(f"  ✅ [知识库列表] 查询到 {len(result)} 个文件\n")
        return {"message": "SUCCESS", "result": result}

    async def parse_web_page(self, url: str):
        print(f"\n  ┌─ [FileService.parse_web_page] 开始抓取网页")
        print(f"  │  URL: {url}")

        try:
            downloaded = trafilatura.fetch_url(url)
            if not downloaded:
                raise Exception("无法获取网页内容")

            text_content = trafilatura.extract(downloaded, include_links=False, include_images=False)
            import re
            title_match = re.search(r"<title>(.*?)</title>", downloaded, re.IGNORECASE)
            title = title_match.group(1) if title_match else url

            if not text_content or len(text_content) < 50:
                raise Exception("无法从该网页提取到有效内容")

            print(f"  │  ✅ 正文提取完成，标题: {title}")
            print(f"  │     正文长度: {len(text_content)} 字")
            print(f"  └─ [FileService.parse_web_page] 网页解析完毕\n")
            return {"title": title, "textContent": text_content}
        except Exception as e:
            print(f"  └─ [FileService.parse_web_page] ❌ 抓取失败: {e}\n")
            raise Exception(f"无法访问该网页，请检查链接是否正确：{e}")

    async def save_web_to_knowledge(self, user_id: str, url: str, title: str, text_content: str):
        print(f"  ┌─ [FileService.save_web_to_knowledge] 网页入库开始")

        MAX_CHARS = 20000
        content = text_content
        if len(content) > MAX_CHARS:
            print(f"  │  ⚠️  正文 {len(content)} 字超长，截断为前 {MAX_CHARS} 字")
            content = content[:MAX_CHARS]

        print(f"  │  ▶ 步骤1: 拆分正文 (1500字/块, 重叠200字，与 PDF 入库对齐)")
        text_splitter = RecursiveCharacterTextSplitter(
            chunk_size=1500,
            chunk_overlap=200,
            separators=["\n---\n", "\n[section=", "\n\n", "\n", "。", ".", " ", ""],
        )
        split_document = text_splitter.create_documents([content])
        print(f"  │  ✅ 拆分完成，共 {len(split_document)} 块")

        print(f"  │  ▶ 步骤2: 存入 MongoDB (uploadType=UB)")
        size_kb = f"{(len(content) / 1024):.2f}kb"
        doc = await FileDocument(
            user_id=user_id,
            file_name=title,
            file_path=f"WEB::{url}",
            file_type="WEB",
            file_size=size_kb,
            file_text=content,
            upload_type="UB",
        ).insert()
        doc_id = str(doc.id)
        print(f"  │  ✅ MongoDB 文档ID = {doc_id}")

        print(f"  │  ▶ 步骤3: 向量化 + 存入 Milvus")
        collection_name = f"_{user_id}"
        if not utility.has_collection(collection_name):
            await self.create_collection(collection_name)

        vectors_doc_title = await self.embeddings_aliyun([title], normalize=True)

        batch_size = 10
        total_batches = (len(split_document) + batch_size - 1) // batch_size
        for i in range(0, len(split_document), batch_size):
            batch = split_document[i:i + batch_size]
            batch_num = i // batch_size + 1
            print(f"  │    处理第 {batch_num}/{total_batches} 批，本批 {len(batch)} 块")
            vectors_doc_text = await self.embeddings_aliyun([item.page_content for item in batch], normalize=True)
            await self.insert_data(collection_name, title, doc_id, batch, vectors_doc_title, vectors_doc_text)
            if i + batch_size < len(split_document):
                await asyncio.sleep(0.5)

        print(f"  │  ✅ 向量化入库完成")
        print(f"  └─ [FileService.save_web_to_knowledge] 网页入库完毕\n")
        return doc_id

    # ── 财报快速解读（OCR + qwen-plus 文本解读） ─────────────────────────
    # 支持类型：image/* (jpg/png/webp/bmp/tiff)、PDF、DOCX、XLSX
    # 1) 走 app/file/ocr.py 抽取文本 + 保留表格结构(markdown 表格)
    # 2) 把抽出的内容送 qwen-plus 按 report_recognition_prompt 解读
    async def recognize_report(self, file_path: str, file_name: str, mimetype: str):
        from app.chat.prompts import report_recognition_prompt
        from app.file.ocr import recognize as ocr_recognize

        print(f"\n  ┌─ [FileService.recognize_report] 开始解读财报文档")
        print(f"  │  文件名: {file_name}")
        print(f"  │  文件类型: {mimetype}")
        print(f"  │  文件路径: {file_path}")

        # 1) OCR / 解析
        try:
            markdown_text = await ocr_recognize(file_path, mimetype)
        except ValueError as e:
            print(f"  └─ [FileService.recognize_report] ❌ 不支持的类型: {e}\n")
            return {
                "reportText": f"暂不支持该文件类型：{mimetype}。请上传 JPG/PNG/WEBP/BMP/TIFF 图片，或 PDF/DOCX/XLSX 文件。",
                "isValid": False,
            }
        except Exception as e:
            print(f"  └─ [FileService.recognize_report] ❌ OCR / 解析失败: {e}\n")
            return {
                "reportText": f"文件解析失败：{e}。请尝试重新上传或换一种格式。",
                "isValid": False,
            }

        if not markdown_text or len(markdown_text.strip()) < 20:
            print(f"  │  ⚠️  抽取到内容过少 ({len((markdown_text or '').strip())} 字)，疑似空白 / 不可识别")
            print(f"  └─ [FileService.recognize_report] 返回无效提示\n")
            return {
                "reportText": "未能从文件中提取到有效内容。如果是扫描件，请确认图片清晰；如果是 PDF/DOCX，请确认文件未加密损坏。",
                "isValid": False,
            }

        print(f"  │  ✅ OCR / 解析完成，共 {len(markdown_text)} 字")

        # 上下文截断（防止后续多轮对话拼太长 — 单次解读不需要全文）
        MAX_OCR_CHARS = 12000
        if len(markdown_text) > MAX_OCR_CHARS:
            print(f"  │  ⚠️  内容过长 ({len(markdown_text)} 字)，截断到前 {MAX_OCR_CHARS} 字")
            markdown_text = markdown_text[:MAX_OCR_CHARS] + "\n\n（内容过长，后续已截断）"

        # 2) qwen-plus 解读成三段 markdown
        print(f"  │  ▶ 调用 qwen-plus 解读 OCR 文本（含表格）")
        completion = self.openai.chat.completions.create(
            model="qwen-plus",
            messages=[
                {"role": "system", "content": report_recognition_prompt},
                {
                    "role": "user",
                    "content": f"以下是从财报文档中通过 OCR 提取的内容（已尽量保留表格结构）：\n\n{markdown_text}",
                },
            ],
        )
        report_text = completion.choices[0].message.content or ""

        print(f"  │  ✅ 解读完成，文本共 {len(report_text)} 字")
        print(f"  └─ [FileService.recognize_report] 财报解读完毕\n")
        return {"reportText": report_text, "isValid": True}

    async def save_report_doc(self, file_path: str, file_name: str, mimetype: str, file_size: int,
                              user_id: str, report_text: str):
        if mimetype == "application/pdf":
            file_type = "PDF"
        elif mimetype.startswith("image/"):
            file_type = "IMG"
        elif mimetype == "application/vnd.openxmlformats-officedocument.wordprocessingml.document":
            file_type = "DOCX"
        elif mimetype == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet":
            file_type = "XLSX"
        else:
            file_type = "FILE"
        size_kb = f"{(file_size / 1024):.2f}kb"
        print(f"  │    [FileService.save_report_doc] 写入 MongoDB: fileName={file_name}, fileType={file_type}, uploadType=UD")
        doc = await FileDocument(
            user_id=user_id,
            file_name=file_name,
            file_path=file_path,
            file_type=file_type,
            file_size=size_kb,
            file_text=report_text,
            upload_type="UD",
        ).insert()
        return str(doc.id)

    async def create_report_session(self, user_id: str, file_name: str, file_type: str,
                                    file_size: str, doc_id: str, report_text: str, redis: aioredis.Redis):
        from app.chat.models import ChatData
        print(f"  │  [FileService.create_report_session] 为财报解读创建会话，写入 MongoDB + Redis")

        user_message = {
            "role": "user",
            "content": f"这是我上传的财报文档经过识别后的解读内容，请你记住它，后续我会基于它向你提问：\n\n{report_text}",
            "displayContent": "请帮我解读这份财报",
            "uploadFileList": [{"fileName": file_name, "fileSize": file_size, "fileType": file_type, "docId": doc_id}],
        }
        assistant_message = {
            "role": "assistant",
            "content": report_text,
        }
        chat_list = [user_message, assistant_message]

        new_chat = await ChatData(user_id=user_id, chat_list=chat_list).insert()
        redis_key = f"chat_history:{user_id}:{new_chat.id}"
        await redis.set(redis_key, json.dumps(chat_list, ensure_ascii=False), ex=10800)

        print(f"  │  ✅ 财报会话已创建，sessionId={new_chat.id}")
        return str(new_chat.id)

    # ══════════════════════════════════════════════════════════════════════════
    # M8: 共享 collection 支持（golden set 100 份财报专用）
    # 设计原则：
    #   1. 与线上单用户 collection 完全隔离，命名不带 `_` 前缀。
    #   2. metadata 字段直接进 Milvus scalar field，支持精确过滤（company/ticker/year/section）。
    #   3. 默认单公司过滤（金融场景默认聚焦一家公司），去掉过滤即跨公司对比。
    #   4. 复用 embeddings_aliyun / insert_data / normalize_for_retrieval，不重复造轮子。
    # ══════════════════════════════════════════════════════════════════════════
    SHARED_KB_COLLECTION = "golden_100_reports"

    async def create_shared_collection(self, collection_name: str = None):
        """创建共享财报知识库 collection。带完整 metadata 字段用于 filter。"""
        name = collection_name or self.SHARED_KB_COLLECTION
        print(f"  │    [FileService.create_shared_collection] 创建共享集合: {name}")
        print(f"  │    字段: id/docId/docHash/company/ticker/year/industry/section/pageNum/docTitle/docText + 双 embed")

        fields = [
            FieldSchema(name="id", dtype=DataType.INT64, is_primary=True, auto_id=True),
            FieldSchema(name="docId", dtype=DataType.VARCHAR, max_length=100),
            FieldSchema(name="docHash", dtype=DataType.VARCHAR, max_length=64),      # PDF 文件 hash（去重）
            FieldSchema(name="company", dtype=DataType.VARCHAR, max_length=200),      # 公司全名
            FieldSchema(name="ticker", dtype=DataType.VARCHAR, max_length=20),        # 股票代码
            FieldSchema(name="year", dtype=DataType.INT64),                           # 财年
            FieldSchema(name="industry", dtype=DataType.VARCHAR, max_length=100),
            FieldSchema(name="section", dtype=DataType.VARCHAR, max_length=20),       # mda/risk/audit/financials/appendix/toc/body
            FieldSchema(name="pageNum", dtype=DataType.INT64),                        # 起始页码（-1 表示未知）
            FieldSchema(name="docTitle", dtype=DataType.VARCHAR, max_length=500),
            FieldSchema(name="docText", dtype=DataType.VARCHAR, max_length=9000),
            FieldSchema(name="embedDocTitle", dtype=DataType.FLOAT_VECTOR, dim=1536),
            FieldSchema(name="embedDocText", dtype=DataType.FLOAT_VECTOR, dim=1536),
        ]
        schema = CollectionSchema(fields=fields, description="Golden 100 financial reports shared KB")
        Collection(name=name, schema=schema)

        collection = self._get_collection(name)
        # scalar 字段索引：company/ticker/year/section 用于 metadata 过滤
        index_params = [
            {"field_name": "id", "index_type": "AUTOINDEX"},
            {"field_name": "docId", "index_type": "AUTOINDEX"},
            {"field_name": "docHash", "index_type": "AUTOINDEX"},
            {"field_name": "company", "index_type": "AUTOINDEX"},
            {"field_name": "ticker", "index_type": "AUTOINDEX"},
            {"field_name": "year", "index_type": "AUTOINDEX"},
            {"field_name": "section", "index_type": "AUTOINDEX"},
            {"field_name": "embedDocTitle", "index_type": "AUTOINDEX", "metric_type": "COSINE"},
            {"field_name": "embedDocText", "index_type": "AUTOINDEX", "metric_type": "COSINE"},
        ]
        for idx in index_params:
            collection.create_index(**idx)
        collection.release()
        print(f"  │    [FileService.create_shared_collection] ✅ 共享集合创建完毕")

    def _parse_section_from_chunk(self, text: str) -> tuple:
        """从 chunk 首行提取 [section=xxx page=N] marker → (section, page_num)。
        找不到就返回 ('body', -1)。marker 在 chunk 里保留（不删除），因为
        它是给 LLM 看的上下文信号，同时字段化后可 metadata 过滤。
        """
        import re
        m = re.search(r"\[section=(\w+)\s+page=(\d+)\]", text[:200])
        if m:
            return m.group(1), int(m.group(2))
        return "body", -1

    async def vector_storage_shared(
        self,
        file_name: str,
        split_document: List[LangchainDocument],
        doc_id: str,
        meta: dict,
        collection_name: str = None,
    ):
        """把切好的 chunks 入共享 collection，带 metadata。
        meta 必须包含: hash/company/ticker/fiscal_year/industry
        """
        name = collection_name or self.SHARED_KB_COLLECTION
        if not utility.has_collection(name):
            await self.create_shared_collection(name)

        print(f"  │    [vector_storage_shared] 集合={name} docId={doc_id} company={meta.get('company')} chunks={len(split_document)}")

        vectors_doc_title = await self.embeddings_aliyun([file_name], normalize=True)
        title_vec = vectors_doc_title[0].embedding

        batch_size = 25
        total = (len(split_document) + batch_size - 1) // batch_size
        for i in range(0, len(split_document), batch_size):
            batch = split_document[i:i + batch_size]
            batch_num = i // batch_size + 1
            print(f"  │    [vector_storage_shared] 批 {batch_num}/{total}，{len(batch)} 块")
            vectors_doc_text = await self.embeddings_aliyun([b.page_content for b in batch], normalize=True)

            entities = []
            for j, item in enumerate(batch):
                section, page_num = self._parse_section_from_chunk(item.page_content)
                entities.append({
                    "docId": doc_id,
                    "docHash": str(meta.get("hash", "")),
                    "company": str(meta.get("company", ""))[:200],
                    "ticker": str(meta.get("ticker", ""))[:20],
                    "year": int(meta.get("fiscal_year", 0) or 0),
                    "industry": str(meta.get("industry", ""))[:100],
                    "section": section,
                    "pageNum": page_num,
                    "docTitle": file_name[:500],
                    "docText": item.page_content[:9000],
                    "embedDocTitle": title_vec,
                    "embedDocText": vectors_doc_text[j].embedding,
                })

            self._ensure_collection_loaded(name)
            collection = self._get_collection(name)
            max_retry = 5
            for attempt in range(1, max_retry + 1):
                try:
                    res = collection.insert(entities)
                    if res.insert_count == len(entities):
                        break
                    if attempt < max_retry:
                        await asyncio.sleep(attempt)
                        continue
                    raise Exception(f"insert incomplete: {res.insert_count}/{len(entities)}")
                except Exception as e:
                    if attempt < max_retry:
                        print(f"  │    [vector_storage_shared] ⏳ 重试 {attempt}: {e}")
                        await asyncio.sleep(attempt)
                        continue
                    raise

        print(f"  │    [vector_storage_shared] ✅ 完成入库 {file_name}")
        return file_name

    async def search_shared_database(
        self,
        user_question: str,
        question_vector: list,
        company: str = None,
        ticker: str = None,
        year: int = None,
        section: str = None,
        companies: list = None,  # 新：多值 company（通用支持 comparison intent）
        tickers: list = None,    # 新：多值 ticker
        years: list = None,      # 新：多值 year
        top_k: int = 18,
        collection_name: str = None,
    ):
        """共享 collection 检索。默认可传 company/ticker/year/section 做 metadata 过滤。
        - 单公司过滤：传 company 或 ticker（单值），或 companies/tickers（多值）。
        - 跨公司对比：传 companies=["A","B"] 或 tickers=["X","Y"]。
        - 分区聚焦：传 section='risk' 只在 Risk Factors 里找。
        - 实体归一化策略（通用，不打表）：
          * company 用 `like "%X%"` 模糊匹配（覆盖用户口语化 vs 库里全称的差异，如 "Weis Markets" ⊂ "Weis Markets, Inc."）
          * 同时 company/ticker 之间用 OR 兜底（用户可能只给了公司名但没给 ticker，反之亦然）
          * filter 命中 0 条 → 退化为无 filter 向量检索（防止过度过滤把好答案也过掉）
        """
        name = collection_name or self.SHARED_KB_COLLECTION
        print(f"\n  ┌─ [search_shared_database] 共享库检索")
        print(f"  │  question: {user_question}")

        # 归一化：单值/多值合并成 list，方便统一处理
        company_list = [c for c in (companies or []) if c]
        if company and company not in company_list:
            company_list.append(company)
        ticker_list = [t.upper() for t in (tickers or []) if t]
        if ticker and ticker.upper() not in ticker_list:
            ticker_list.append(ticker.upper())
        year_list = list(years or [])
        if year and year not in year_list:
            year_list.append(int(year))

        print(f"  │  filter entities: companies={company_list} tickers={ticker_list} years={year_list} section={section}")

        # 构造 expr（Milvus scalar filter）
        # 通用规则：
        #   - company/ticker 属于"实体维度"，两者取 OR（用户可能任填一个）
        #   - year/section 属于"过滤维度"，与实体维度取 AND
        entity_conds = []
        for c in company_list:
            # like 用 %...% 覆盖 "Weis Markets" ⊂ "Weis Markets, Inc." 之类
            # 对 Milvus VARCHAR 用 double quote 转义防注入（company 名一般没有引号，安全）
            safe = c.replace('"', '\\"')
            entity_conds.append(f'company like "%{safe}%"')
        for t in ticker_list:
            safe = t.replace('"', '\\"')
            entity_conds.append(f'ticker == "{safe}"')

        conds = []
        if entity_conds:
            conds.append("(" + " or ".join(entity_conds) + ")")
        if year_list:
            year_conds = " or ".join(f"year == {int(y)}" for y in year_list)
            conds.append("(" + year_conds + ")")
        if section:
            safe = section.replace('"', '\\"')
            conds.append(f'section == "{safe}"')
        expr = " and ".join(conds) if conds else None
        print(f"  │  Milvus expr: {expr}")

        self._ensure_collection_loaded(name)
        collection = self._get_collection(name)

        def _do_search(_expr):
            req_title = AnnSearchRequest(
                data=[question_vector], anns_field="embedDocTitle",
                param={"metric_type": "COSINE"}, limit=top_k, expr=_expr,
            )
            req_text = AnnSearchRequest(
                data=[question_vector], anns_field="embedDocText",
                param={"metric_type": "COSINE"}, limit=top_k, expr=_expr,
            )
            return collection.hybrid_search(
                reqs=[req_title, req_text],
                rerank=WeightedRanker(0.3, 0.8),
                limit=top_k,
                output_fields=["docTitle", "docText", "company", "ticker", "year", "section", "pageNum"],
            )

        results = _do_search(expr)
        hits = results[0] if results else []
        print(f"  │  ✅ Milvus 命中 {len(hits)} 条 (with filter)")

        # 通用兜底：filter 太严命中太少，降级到无 filter
        if expr and len(hits) < 2:
            print(f"  │  ⚠️ filter 命中 <2 条，降级为无 filter 向量检索")
            results = _do_search(None)
            hits = results[0] if results else []
            print(f"  │  ✅ Milvus 命中 {len(hits)} 条 (no filter)")

        # 关键词过滤（软策略）：
        # - 有 metadata 精确过滤时，向量语义召回已经很聚焦，关键词过滤是"锦上添花"
        # - 如果过滤后剩 <2 条，说明关键词过滤过严（LLM 抽的关键词可能没字面出现），
        #   直接放弃过滤，返回向量语义结果（用户第一性原理：不能因某词没字面出现导致整体不可用）
        # - 只在无 metadata 过滤 且 命中数很多时，关键词过滤才作为二次收窄
        MIN_KEEP_AFTER_KW = 2
        if hits:
            try:
                kws = await self.extract_keywords(user_question)
                filtered = self.filter_docs_by_keywords(hits, kws["keyWord"])
                if len(filtered) < MIN_KEEP_AFTER_KW:
                    print(f"  │  ⚠️ 关键词过滤后仅 {len(filtered)} 条 (<{MIN_KEEP_AFTER_KW})，放弃过滤，用向量召回原始结果")
                    filtered = hits
                else:
                    print(f"  │  ✅ 关键词过滤后 {len(filtered)} 条")
            except Exception as e:
                print(f"  │  ⚠️ 关键词提取失败 {e}，直接用向量召回结果")
                filtered = hits
        else:
            filtered = []

        # 组装返回
        search_doc_title, search_doc_text = [], ""
        if filtered:
            search_doc_title = list({item.entity.get("docTitle", "") for item in filtered})
            for idx, item in enumerate(filtered):
                ent = item.entity
                # 给 LLM 的 chunk 头部注入 metadata 提示
                header = f"[{ent.get('company', '')} {ent.get('year', '')} {ent.get('section', '')} p.{ent.get('pageNum', '?')}]"
                search_doc_text += f"{idx + 1}.{header}{ent.get('docText', '')}\n"
        else:
            search_doc_text = "&没有检索到相关文档&"

        print(f"  └─ [search_shared_database] 返回 {len(search_doc_title)} 篇\n")
        return {
            "searchDocTitle": search_doc_title,
            "searchDocText": f"请根据检索到的知识库文档内容回复用户问题,用户问题:{user_question};\n文档内容:{search_doc_text}",
        }


file_service = FileService()