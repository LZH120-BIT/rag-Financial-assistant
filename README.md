# 财报智能问答 RAG 系统

面向上市公司财报（10-K / 年报）的中英双语智能问答系统。基于 FastAPI + Milvus + 通义千问构建，包含自研文档解析管线、检索增强生成（RAG）链路与评测体系。

## 功能特性

- **知识库检索问答**：意图理解 → 向量检索 → 关键词过滤 → 生成答案的完整 RAG 链路
- **财报文档解析**：自研 OCR 管线，支持表格结构恢复、多栏排版切分、页眉页脚去重、图片文字识别与分区打标
- **多轮对话**：支持上下文续问与指代改写，Redis 会话缓存
- **合规管控**：投资建议与非金融问题自动拒答
- **文档 / 网页入库**：支持 PDF、DOCX、XLSX 及网页正文入库
- **评测体系**：对齐 RAGAS 主流指标，并针对金融数字精确性、时间范围、合规红线做专项加强

## 技术栈

| 层 | 技术 |
|---|---|
| 后端 | FastAPI、Beanie（MongoDB ODM）、pydantic-settings |
| 向量检索 | Milvus 2.5（双向量混合检索 + 标量字段过滤） |
| 模型 | 通义千问 qwen-plus（生成）、text-embedding-v2（向量化，1536 维） |
| 文档解析 | RapidOCR、RapidTable、PyMuPDF、python-docx、openpyxl |
| 存储 | MongoDB（持久化）、Redis（会话缓存） |
| 前端 | Vue 3 + TypeScript + Vite + Element Plus + Pinia |

## 目录结构

```
rag-fastapi/         FastAPI 后端
  app/
    chat/            对话与 RAG 检索链路
    file/            文档解析、OCR、向量化、检索
    user/            用户与鉴权
    middleware/      鉴权、异常、响应封装
  main.py            应用入口
  docker-compose.yml 依赖服务编排（Milvus / MongoDB / Redis）
project-user/        Vue 3 前端
evaluation/          评测题集与评测报告
scripts/             批量入库、评测、诊断脚本
research/            文档特征调研
EVALUATION.md        评测指标体系说明
```

## 快速开始

### 1. 启动依赖服务

```bash
cd rag-fastapi
docker compose up -d   # Milvus / MongoDB / Redis
```

### 2. 配置环境变量

```bash
cp rag-fastapi/.env.example rag-fastapi/.env
# 编辑 .env，填入 TONGYI_AKI_KEY 等配置
```

### 3. 启动后端

```bash
cd rag-fastapi
pip install -r requirements.txt
uvicorn main:app --reload
```

### 4. 启动前端

```bash
cd project-user
npm install
npm run dev
```

## 检索链路概览

```
用户问题
  → 意图理解（实体抽取 / 续问改写 / 合规判定）
  → 合规拒答短路（投资建议 / 非金融问题）
  → 文本归一化 + 向量化
  → Milvus 混合检索（标题向量 + 内容向量，加权融合）+ 元数据过滤
  → 关键词二次过滤
  → 拼装上下文 → qwen-plus 流式生成
```

## 评测

评测指标体系见 [EVALUATION.md](EVALUATION.md)，涵盖通用 RAG 指标（RAGAS）、金融特化指标、规则匹配与性能指标共 13 项。评测题集与报告见 `evaluation/` 目录。

> 注：财报数据集（`golden_set/`）与模型权重等大文件未纳入版本管理。
