# Poem RAG

诗词知识库与 RAG 问答项目。

当前已建立：

1. FastAPI 异步后端和统一 API 响应契约。
2. MySQL 用户模型、JWT 登录、Token 刷新和管理员权限依赖。
3. Vue 3、TypeScript、Vue Router、Pinia 前端。
4. 诗词浏览、搜索、登录、注册和个人页面。
5. 结构化语料导入、版本快照、结构切块、词法检索和检索评估基线。
6. Qwen Embedding Provider，支持批量、维度、超时和有限重试配置。
7. Qdrant 最小索引闭环，支持 Collection 校验、向量 upsert、chunk 映射和失败补偿。
8. 内部 Dense 检索 Service，支持 Qdrant 召回、元数据过滤和 MySQL 可见性回查。
9. 内部 Hybrid RRF 检索 Service，融合词法与 Dense 候选并保留来源匹配类型。
10. 内部查询改写与多查询 RRF，支持月亮、思乡和已知作者实体的确定性扩展。
11. 真实 Qdrant 适配器烟测，覆盖临时 Collection、维度校验、upsert、过滤检索和删除。
12. 用户会话、消息、引用快照和会话所有权校验。
13. DeepSeek Chat Provider 与最小 LangGraph 问答流：查询改写、证据检索、LLM 结构化可答性判定、受证据约束生成和回答校验。
14. POST SSE 流式问答、Vue 问答页、引用卡片、停止生成，以及无证据或判定不可答时的稳定拒答。
15. pytest、Ruff、Vitest 和前端生产构建基线。
16. DeepSeek Chat Provider 已完成真实 `deepseek-chat` 流式与 JSON 模式烟测，并完成真实 MySQL + SSE 基础在线联调：有证据问题返回带引用回答，无答案问题稳定拒答且不产生引用。

## 文档入口

建议先阅读：

1. [文档导航与维护规则](docs/README.md)
2. [项目说明与代码导览](docs/PROJECT_GUIDE.md)
3. [开发流程与工程约定](docs/DEVELOPMENT_WORKFLOW.md)
4. [前后端接口契约](docs/FRONTEND_BACKEND_CONTRACT.md)
5. [开发日志](docs/DEVELOPMENT_LOG.md)

接口的实际运行时定义以 FastAPI `/openapi.json` 和 `/docs` 为准，目标契约以
`docs/FRONTEND_BACKEND_CONTRACT.md` 为准。两者不一致时应先修复差异，再继续扩展功能。

## 本地开发

后端：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r apps\api\requirements-dev.txt
.\.venv\Scripts\python.exe -m uvicorn app.main:app --reload --app-dir apps\api
```

前端：

```powershell
pnpm --dir apps/web install
pnpm --dir apps/web dev
```

默认地址：

```text
前端：http://127.0.0.1:5173
后端：http://127.0.0.1:8000
OpenAPI：http://127.0.0.1:8000/docs
```

数据库迁移：

```powershell
cd apps\api
..\..\.venv\Scripts\python.exe -m alembic upgrade head
```

结构化语料导入预检：

```powershell
.\.venv\Scripts\python.exe apps\api\scripts\import_corpus.py `
  --input data\import\example_corpus_v1.json `
  --dry-run
```

`--dry-run` 只校验 JSON 和字段约束，不连接数据库或写入数据。正式导入的 JSON
契约、报告格式和发布语义见
[结构化语料导入设计](docs/features/20260919-open-licensed-corpus-import.md)。

## 基础验证

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m ruff check apps\api
.\.venv\Scripts\python.exe apps\api\scripts\import_corpus.py --input data\import\example_corpus_v1.json --dry-run
.\.venv\Scripts\python.exe apps\api\scripts\evaluate_retrieval.py --top-k 5
pnpm --dir apps\web typecheck
pnpm --dir apps\web test
pnpm --dir apps\web build
```

`--top-k` 默认评估 `lexical-baseline-v1`。不依赖 Qdrant 或 Qwen 的查询改写对照
可执行 `--strategy expanded`；真实 Qdrant 已就绪，真实 Qwen 配置确认后可使用
`--strategy dense` 或 `--strategy hybrid` 执行同一评估集的内部对比。这些内部策略
尚未切换公开 HTTP，`GET /api/v1/search/evidence` 仍固定使用词法基线。

Qdrant 与 Qwen Provider 烟测：

```powershell
.\.venv\Scripts\python.exe apps\api\scripts\smoke_qdrant.py
.\.venv\Scripts\python.exe apps\api\scripts\smoke_qwen_embedding.py --text 明月
.\.venv\Scripts\python.exe apps\api\scripts\smoke_deepseek_chat.py
```

Qdrant 烟测始终使用随机临时 Collection 并在结束后清理；Qwen 烟测需要真实
DashScope 网络和有效 Key；DeepSeek 烟测只报告模型、模式、增量片段数、字符数和
耗时，不输出完整回答。三个脚本都不会输出向量内容、上游响应正文或密钥。

验证 `assess` 依赖的非流式 JSON object 输出：

```powershell
.\.venv\Scripts\python.exe apps\api\scripts\smoke_deepseek_chat.py --json
```

2026-09-20 本轮真实烟测结果：流式模式 `model=deepseek-chat`、
`delta_count=41`、`char_count=63`、`elapsed_ms=982.91`；JSON 模式
`model=deepseek-chat`、`delta_count=0`、`char_count=63`、`elapsed_ms=444.78`。
这些结果只证明 Provider 与真实服务已连通，不代表生成质量或端到端问答准确率。

2026-09-20 基础在线联调结果：

1. `请结合诗句说明《静夜思》里明月和思乡的关系。` 返回
   `meta -> retrieval -> delta* -> citation -> done`，耗时 `2527.81 ms`，策略
   `expanded-lexical-v1`，候选 `10` 条、入选 `5` 条，回答包含 `[1]`，消息状态为
   `completed`，持久化引用 `1` 条。
2. `李白的出生地在哪里？` 返回 `meta -> retrieval -> delta -> done`，耗时
   `1074.54 ms`，返回固定拒答文案，引用事件 `0` 条，消息状态为 `completed`，
   持久化引用 `0` 条。

该验收覆盖真实 HTTP/SSE 事件、真实模型调用和 MySQL 持久化；PowerShell 客户端会
缓冲 SSE 响应，浏览器实时逐块渲染仍由前端测试覆盖。

Qwen 和 Qdrant 均可用后，可执行真实 chunk 索引：

```powershell
.\.venv\Scripts\python.exe apps\api\scripts\index_chunks.py --all-pending
.\.venv\Scripts\python.exe apps\api\scripts\index_chunks.py --version-id 1
```

CLI 默认直接索引已存在的 `structural-v1` chunks，不重建切块；`--all-pending`
按版本处理所有待索引 chunks，每个版本都会写入一条 `poem_index_runs` 记录。

查询改写词典位于 `data/query_expansion/lexicon_v1.json`，加载时会校验版本、概念、
触发词、扩展词和实体重复项。

问答模型通过以下环境变量配置：

```text
DEEPSEEK_API_KEY=
DEEPSEEK_BASE_URL=https://api.deepseek.com
DEEPSEEK_CHAT_MODEL=deepseek-chat
```

未配置有效 Chat Provider 时，问答流接口返回 `503 CHAT_MODEL_NOT_CONFIGURED`，不会
持久化半成品消息。当前仓库默认模型 ID 是 `deepseek-chat`；“DeepSeek 4.1 Flash”的
精确可用模型 ID 仍需通过真实账号和供应商文档确认后再写入配置。
