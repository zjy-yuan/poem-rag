# Qwen Embedding Provider

> 状态：已实现（Provider 层）  
> 创建日期：2026-09-19  
> 最近更新：2026-09-19（已接入最小索引和 Dense 检索 Service）  
> 关联任务：在接入 Qdrant 前，用可替换适配器封装 Qwen Embedding 的批处理、维度、超时和重试边界

## 1. 背景与问题

当前 RAG 语料已经具备结构化导入、不可变版本、`structural-v1` chunks、索引运行元数据和 MySQL 词法检索基线，但还没有任何真实 Embedding 调用。直接把 DashScope SDK 或 HTTP 细节写进索引 Service 会带来以下问题：

1. 单元测试必须依赖真实网络和 API Key。
2. 模型 ID、维度、批量和限流参数散落在业务代码中，难以替换和审计。
3. 供应商错误、超时、限流和响应格式异常无法统一处理。
4. Qdrant 接入后很难区分失败来自 Embedding、向量写入还是数据库事务。

因此先建立只依赖 HTTP 协议的 `EmbeddingProvider`，让索引编排依赖接口而不是供应商 SDK。

## 2. 目标

1. 定义 `EmbeddingProvider` 协议，统一文档批量向量化和查询向量化。
2. 实现 DashScope OpenAI-compatible `/embeddings` 适配器。
3. 支持模型 ID、维度、批量大小、超时、最大重试和退避时间配置。
4. 对网络错误、`408/429/5xx` 做有限重试；对 `401/403` 等不可重试错误快速失败。
5. 校验返回数量、索引、空向量和维度一致性。
6. 测试使用注入的 `httpx.MockTransport`，不访问外网、不需要真实 API Key。
7. 为后续 `poem_chunks` 写入和 Qdrant upsert 提供稳定接口。

## 3. 非目标

1. 不实现 Qdrant Collection、点写入、过滤检索或对账。
2. 不把向量写入 `poem_chunks.vector_id`，也不改变 chunk 状态。
3. 不实现索引 Worker、队列、租约、超时回收或任务取消。
4. 不调用真实 DashScope API，不验证账号额度、限流上限和真实计费。
5. 不实现 Embedding 缓存、批量并发、Token 估算或成本统计。
6. 不实现查询改写、混合检索、Rerank 或 LangGraph。

## 4. 用户场景与验收标准

| 场景 | 操作 | 预期结果 |
| --- | --- | --- |
| 批量文档 | 输入 3 条文本、批量大小 2 | 发起 `2 + 1` 两次请求，保持原始顺序 |
| 查询向量 | 输入一条查询 | 返回单条向量 |
| 空输入 | 文档列表为空 | 直接返回空列表，不发请求 |
| 可重试错误 | 首次返回 `429` | 按退避策略重试并返回后续成功结果 |
| 不可重试错误 | 返回 `401` | 只请求一次并抛出带状态码的错误 |
| 响应错序 | 返回 `index` 乱序 | 按索引重排，结果顺序与输入一致 |
| 数量不一致 | 返回向量数少于输入数 | 抛出可诊断的 Provider 错误 |
| 维度不一致 | 返回向量长度不一致或与配置不符 | 抛出 Provider 错误 |
| 空维度配置 | `.env` 使用 `QWEN_EMBEDDING_DIMENSION=` | 规范化为 `None`，不阻止应用启动 |

## 5. 方案概览

```text
Index Service（后续）
  -> EmbeddingProvider 协议
       -> QwenEmbeddingProvider
            -> httpx.AsyncClient
            -> DashScope OpenAI-compatible /embeddings
                 -> 批量拆分
                 -> 可重试错误退避
                 -> 数量和维度校验
  -> Qdrant upsert（后续）
```

关键取舍：

1. 使用通用 HTTP 协议而不是 DashScope SDK，减少业务层对供应商对象的依赖。
2. 首版只做有限重试，不引入通用重试框架；Embedding 调用量较高时再评估退避、抖动和熔断。
3. 维度默认允许为空，因为模型默认维度可能随供应商配置变化；真正写入索引前必须锁定维度。
4. 批量按顺序串行执行，先保证可测试性和错误定位；后续再评估受控并发。
5. Provider 错误保留 `retryable` 和 `status_code`，便于后续索引运行记录错误分类。

## 6. 接口与契约

本切片不新增 HTTP API，也不新增数据库迁移。

内部协议：

```python
class EmbeddingProvider(Protocol):
    @property
    def model(self) -> str: ...

    @property
    def dimension(self) -> int | None: ...

    async def embed_documents(self, texts: list[str]) -> list[list[float]]: ...

    async def embed_query(self, text: str) -> list[float]: ...
```

配置：

| 环境变量 | 默认值 | 说明 |
| --- | --- | --- |
| `DASHSCOPE_API_KEY` | 空 | DashScope API Key，不写入日志或数据库 |
| `DASHSCOPE_BASE_URL` | OpenAI-compatible Base URL | 适配器在其后追加 `/embeddings` |
| `QWEN_EMBEDDING_MODEL` | `text-embedding-v4` | 可替换模型 ID，正式使用前需核对账号可用性 |
| `QWEN_EMBEDDING_DIMENSION` | 空 | 留空使用模型默认维度；写入索引前必须确认 |
| `QWEN_EMBEDDING_BATCH_SIZE` | `10` | 单次请求最大文本数 |
| `QWEN_EMBEDDING_TIMEOUT_SECONDS` | `30` | 单次 HTTP 请求超时 |
| `QWEN_EMBEDDING_MAX_RETRIES` | `2` | 可重试错误的最大额外请求次数 |
| `QWEN_EMBEDDING_RETRY_BACKOFF_SECONDS` | `0.5` | 指数退避基数，测试中可设为 `0` |

请求体：

```json
{
  "model": "text-embedding-v4",
  "input": [
    "床前明月光",
    "疑是地上霜"
  ],
  "dimensions": 1024
}
```

`dimensions` 只有在配置了维度时才发送。

## 7. 数据设计

本切片不新增表。后续索引写入需要把以下信息映射到现有字段：

1. `poem_chunks.embedding_model`：实际模型 ID。
2. `poem_chunks.embedding_dimension`：实际返回维度。
3. `poem_chunks.vector_id`：Qdrant 点 ID，必须唯一且可追踪。
4. `poem_index_runs.config_snapshot`：模型、批量、超时等非敏感配置。

`DASHSCOPE_API_KEY` 不得进入 `config_snapshot`、异常报告或数据库。

## 8. 后端设计

新增：

1. `app/ai/providers/embedding.py`：协议定义。
2. `app/ai/providers/qwen_embedding.py`：配置、错误和 DashScope 适配器。
3. `app/ai/providers/__init__.py`：公开导入边界。
4. `apps/api/tests/test_qwen_embedding.py`：批量、重试、错误和响应校验测试。

错误规则：

| 条件 | 行为 |
| --- | --- |
| 网络错误、`408`、`429`、`500/502/503/504` | 可重试，达到上限后抛出 |
| `401/403/404/422` 等 | 不可重试，直接抛出 |
| 非 JSON、数量不符、索引无效、空向量、维度不符 | 抛出 `EmbeddingProviderError` |
| 输入为空 | 文档批量直接返回空；查询文本为空拒绝 |

## 9. 前端设计

本切片不涉及前端。

## 10. RAG 与评估

Provider 本身不改变检索策略，因此不修改固定评估集。后续接入索引后需要：

1. 用同一批 `structural-v1` chunks 生成向量并记录真实模型 ID、维度和批量。
2. 先做 Dense-only 检索，再与 `lexical-baseline-v1` 比较 Recall@k、MRR 和拒答准确率。
3. 记录平均请求数、重试次数、延迟和失败率，不能只报告最终答案。

## 11. 测试计划

| 层级 | 覆盖内容 |
| --- | --- |
| 单元测试 | 批量拆分、请求体、乱序响应、空输入 |
| 集成测试 | Mock HTTP Transport 的可重试和不可重试错误 |
| 契约测试 | 配置映射、模型 ID、维度和空值边界 |
| E2E | 本切片不涉及 |
| 评估 | 本切片不改变检索策略 |

验证结果：

1. Provider 专项测试：`8 passed`，有 2 条第三方测试客户端弃用警告。
2. Ruff：通过。
3. mypy（Provider 与配置）：通过。
4. 空维度环境变量解析：返回 `None`。

## 12. 风险与回滚

1. `text-embedding-v4` 的默认维度、上下文上限、批量和限流尚未通过真实账号验证。
2. DashScope 的 OpenAI-compatible 协议可能存在供应商差异；真实接入前需要用小规模请求核对字段和错误格式。
3. 当前重试没有抖动和全局并发限制，高并发时可能放大供应商限流。
4. 返回维度未与数据库中的历史维度比对，跨模型混用可能产生不可比较的向量。
5. 当前不缓存 Embedding，重复重建会增加调用成本和延迟。
6. 回滚方式是停止注入 Provider 并继续使用词法检索；本切片没有修改现有接口、表或检索路径。

## 13. 实施任务

- [x] 定义 `EmbeddingProvider` 协议
- [x] 实现 Qwen Embedding OpenAI-compatible 适配器
- [x] 增加批量、重试、错误和维度校验
- [x] 增加配置项和 `.env.example`
- [x] 使用 Mock Transport 完成专项测试
- [x] 回写功能索引、项目说明、契约和开发日志
- [ ] 用真实 DashScope 小规模请求核对模型 ID、维度、批量、限流和错误格式
- [x] 实现 chunks -> embeddings -> Qdrant upsert
- [x] 实现 Dense-only 检索 Service
- [ ] 使用真实 Qdrant 和 Qwen 记录 Dense-only 与词法基线的同集评估对比
- [ ] 评估缓存、并发、抖动、熔断和成本统计

## 14. 决策与变更记录

| 日期 | 决策或变化 | 原因 |
| --- | --- | --- |
| 2026-09-19 | Provider 使用 HTTP 协议，不依赖 DashScope SDK | 保持业务层可替换、测试无网络依赖 |
| 2026-09-19 | 维度默认允许为空，但索引前必须确认 | 模型默认维度未验证，不能提前伪造常量 |
| 2026-09-19 | 只对网络错误和 `408/429/5xx` 重试 | 鉴权和参数错误重试无意义 |
| 2026-09-19 | 使用注入的 `httpx.AsyncClient` | 测试可控，生产仍可统一配置超时和连接池 |
| 2026-09-19 | 空 `QWEN_EMBEDDING_DIMENSION` 规范化为 `None` | 保证 `.env.example` 可直接启动 |
