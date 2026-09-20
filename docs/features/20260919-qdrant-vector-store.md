# Qdrant 向量存储与最小索引闭环

> 状态：已实现（最小索引链路与可执行 CLI）  
> 创建日期：2026-09-19  
> 最近更新：2026-09-19（真实 Qdrant 烟测已通过）  
> 关联任务：把已持久化的 `structural-v1` chunks 通过 Qwen Embedding 写入 Qdrant，并保存可追踪映射

## 1. 背景与问题

项目已经完成 Qwen Embedding Provider、`structural-v1` chunks、索引运行元数据和 MySQL 词法检索基线，但 chunks 还没有真正生成和保存向量。

如果直接在一个函数里同时处理切块、HTTP、Qdrant SDK、数据库事务和错误恢复，会产生以下问题：

1. Qdrant SDK 细节泄漏到业务 Service，无法用 Fake 独立测试。
2. Embedding 数量或维度异常时可能写入不一致数据。
3. Qdrant 已成功写入、MySQL 回写失败时，数据库与向量库会出现不可见漂移。
4. 无法区分失败来自 Provider、向量存储还是数据库事务。
5. 后续重试、对账和 active index 切换缺少运行记录。

## 2. 目标

1. 定义 `VectorStorePort`，隔离 Collection 创建、点写入和点删除。
2. 实现 `QdrantVectorStore`，固定使用 `dense` named vector。
3. 实现 `IndexingService.index_version()`，串联 chunks、Embedding、Qdrant upsert 和 MySQL 回写。
4. 使用确定性 UUID5 作为 Qdrant point ID，避免重复执行产生随机重复点。
5. 记录 `vector_id`、`embedding_model`、`embedding_dimension` 和 chunk 状态。
6. 对维度、数量、Collection 配置和重复索引做显式校验。
7. Qdrant upsert 失败时不做虚假回写；数据库回写失败时尝试删除本次点。
8. 用 Fake Provider、Fake Vector Store 和 SDK 单元测试覆盖边界，不依赖真实网络。

## 3. 非目标

1. 不实现 HTTP 索引任务接口。
2. 不实现 Worker、任务队列、租约、取消、超时回收或自动重试。
3. 本切片不实现 Dense 检索、混合检索、RRF、Rerank 或 LangGraph；Dense 检索已在后续切片实现为内部 Service。
4. 不实现旧向量清理、active index 切换或全量对账。
5. 不调用真实 DashScope；Qdrant 真实联调通过独立烟测脚本执行，不混入普通单元测试。
6. 不保存 chunk 完整正文到 Qdrant payload，正文仍以 MySQL 为准。

## 4. 用户场景与验收标准

| 场景 | 操作 | 预期结果 |
| --- | --- | --- |
| 首次索引 | 对 pending chunks 执行索引 | 生成向量、创建 Collection、upsert 点并回写 chunk |
| 向量数量异常 | Provider 返回数量不一致 | 返回 `EMBEDDING_PROVIDER_ERROR`，不写 Qdrant |
| 向量维度异常 | Provider 返回维度不一致 | 返回 `EMBEDDING_PROVIDER_ERROR`，不写 Qdrant |
| Qdrant 写入失败 | Fake SDK 抛出异常 | 返回 `VECTOR_STORE_ERROR`，chunk 保持 pending |
| 数据库回写失败 | upsert 后模拟 MySQL 异常 | 尝试删除本次 Qdrant 点，运行标记 failed |
| 已有向量映射 | chunk 已有 `vector_id` | 返回 `CHUNKS_ALREADY_INDEXED`，不重复写入 |
| Collection 已存在 | 维度一致 | 复用 Collection |
| Collection 已存在 | 维度或 named vector 不一致 | 返回 `VECTOR_STORE_ERROR` |

## 5. 方案概览

```text
PoemVersion
  -> rebuild structural-v1 chunks（可选）
  -> ChunkRepository.list_indexable_by_version()
  -> EmbeddingProvider.embed_documents()
  -> 校验数量与维度
  -> VectorStore.ensure_collection(dimension)
  -> Qdrant upsert（dense named vector）
  -> ChunkRepository.mark_indexed()
  -> IndexRunService.succeed()
```

关键取舍：

1. MySQL 保存 chunk、版本、状态和向量映射；Qdrant 只保存向量和过滤所需元数据。
2. Qdrant payload 不保存完整正文，避免正文出现两个事实来源。
3. point ID 使用版本、粒度、策略、序号和内容哈希生成 UUID5，保证同版本同内容幂等。
4. 当前先实现同步编排的最小闭环，不提前引入 Worker 和队列。
5. 失败补偿采用“尽力删除”而非假装跨库原子事务；对账机制留到后续切片。

## 6. 接口与契约

本切片不新增或修改 HTTP API，也不新增数据库迁移。

内部端口：

```python
class VectorStorePort(Protocol):
    @property
    def collection(self) -> str: ...

    async def ensure_collection(self, *, dimension: int) -> None: ...

    async def upsert(self, points: list[VectorPoint]) -> None: ...

    async def delete(self, point_ids: list[str]) -> None: ...
```

配置：

| 环境变量 | 默认值 | 说明 |
| --- | --- | --- |
| `QDRANT_URL` | 空 | Qdrant HTTP 地址 |
| `QDRANT_API_KEY` | 空 | 可选 API Key，不写入日志或运行快照 |
| `QDRANT_COLLECTION` | `poem_chunks_v1` | Collection 名称 |
| `QDRANT_TIMEOUT_SECONDS` | `30` | SDK 操作超时 |
| `QDRANT_DISTANCE` | `Cosine` | 支持 `Cosine`、`Euclid`、`Dot` |

Qdrant 点结构：

```json
{
  "id": "uuid5",
  "vector": {
    "dense": [0.1, 0.2]
  },
  "payload": {
    "chunk_id": 1,
    "poem_id": 1,
    "poem_version_id": 1,
    "granularity": "line",
    "chunk_index": 0,
    "chunk_strategy": "structural-v1",
    "content_hash": "...",
    "title": "...",
    "line_start": 1,
    "line_end": 1
  }
}
```

## 7. 数据设计

复用现有字段，不新增表：

| 字段 | 写入内容 |
| --- | --- |
| `poem_chunks.vector_id` | Qdrant point ID |
| `poem_chunks.embedding_model` | 实际 Embedding 模型 ID |
| `poem_chunks.embedding_dimension` | 实际向量维度 |
| `poem_chunks.status` | 成功后为 `ready` |
| `poem_index_runs` | 运行状态、阶段、chunk 数、向量数、维度和配置快照 |

约束：

1. 同一版本存在 `pending` 或 `running` 运行时禁止重复创建。
2. 已有任意 `vector_id` 的版本禁止直接重建。
3. note chunk 必须关联 `published` 注释。
4. `mark_indexed()` 只 flush，最终由运行状态提交统一落库。

## 8. 后端设计

新增：

1. `app/ai/providers/vector_store.py`：向量存储端口和点模型。
2. `app/ai/providers/qdrant.py`：Qdrant 适配器、错误和工厂。
3. `app/services/indexing.py`：最小索引编排和补偿。
4. `apps/api/tests/test_qdrant_vector_store.py`：适配器边界测试。
5. `apps/api/tests/test_indexing.py`：索引闭环和失败补偿测试。

修改：

1. `app/repositories/chunks.py`：增加可索引 chunk 查询和批量回写。
2. `app/services/index_runs.py`：支持写入实际 Embedding 维度。
3. `app/core/errors.py`：增加 Provider 和向量存储错误码。
4. `app/core/config.py`、`.env.example`、`requirements.txt`：增加 Qdrant 配置和依赖。

失败映射：

| 异常 | HTTP 错误码 | 处理 |
| --- | --- | --- |
| `EmbeddingProviderError` | `503 EMBEDDING_PROVIDER_ERROR` | 不写 Qdrant，运行 failed |
| `VectorStoreError` | `503 VECTOR_STORE_ERROR` | 不虚假回写，运行 failed |
| `CHUNKS_ALREADY_INDEXED` | `409` | 不重复写入 |
| 其他异常 | `500 INTERNAL_ERROR` | 尝试补偿并运行 failed |

## 9. 前端设计

本切片不涉及前端，不新增管理端索引按钮或进度展示。

## 10. RAG 与评估

本切片只建立索引能力，不改变线上检索策略，因此不修改 27 条评估集和词法基线。

后续需要：

1. 使用真实 Qwen Embedding 对相同 chunks 生成向量。
2. 使用 Dense-only 检索和同一 27 条样本对比 `lexical-baseline-v1`（Service 已实现，等待真实联调）。
3. 记录召回、MRR、延迟、调用次数、失败率和成本。

## 11. 测试计划

| 层级 | 覆盖内容 |
| --- | --- |
| 单元测试 | Qdrant Collection、点结构、payload、删除和错误包装 |
| 集成测试 | Fake Provider + Fake Vector Store 的完整索引闭环 |
| 失败测试 | 维度异常、upsert 失败、数据库回写失败和重复索引 |
| E2E | 真实 Qdrant 适配器烟测已通过；真实索引 CLI 已实现；Worker 仍待接入 |
| 评估 | Dense 检索 Service 已实现，等待真实 Qwen 后与词法基线同集比较 |

验证结果：

1. 完整后端测试：`65 passed`。
2. Ruff：通过。
3. mypy（AI、索引、chunk 仓储和请求上下文）：通过。
4. 2026-09-19 启动 Qdrant `1.19.1`，`smoke_qdrant.py` 验证临时 Collection
   创建、维度不匹配拒绝、upsert、过滤检索和删除均通过，测试 Collection 已清理。
5. 真实 DashScope/Qwen 调用尚未完成，当前被沙箱网络限制和外网审批故障阻塞。
6. 2026-09-20 新增 `apps/api/scripts/index_chunks.py`，支持按版本或批量处理
   `pending` chunks；脚本通过 Ruff、mypy 和 `--help` 自检，尚未在真实 Qwen
   可用后执行正式索引。

## 12. 风险与回滚

1. MySQL commit 与 Qdrant upsert 无法真正原子提交；当前只有尽力补偿，没有定期对账。
2. 进程在 upsert 和数据库提交之间崩溃时，可能留下无数据库映射的 Qdrant 点。
3. 当前索引 Service 同步执行，大版本会造成长请求；正式使用前需要 Worker、租约和超时回收。
4. 真实 Qwen 模型维度、批量和限流尚未通过账号验证。
5. 旧向量清理、active index 切换和模型切换策略尚未定义。
6. 回滚方式是停止调用 `IndexingService`，继续使用现有词法检索；本切片未改变 HTTP 接口。

## 13. 实施任务

- [x] 定义 `VectorStorePort` 和 `VectorPoint`
- [x] 实现 Qdrant Collection、upsert、delete 和错误包装
- [x] 增加 chunk 可索引查询和索引元数据回写
- [x] 实现 chunks -> Embedding -> Qdrant upsert 最小 Service
- [x] 增加失败补偿和重复索引保护
- [x] 增加 Fake 闭环与 Qdrant 适配器测试
- [x] 回写功能索引、项目说明、契约、README 和开发日志
- [x] 启动真实 Qdrant 并执行 Collection/维度/upsert/query/delete 烟测
- [x] 新增按版本或 `pending` 版本批量执行的索引 CLI
- [ ] 用真实 Qwen Embedding 验证模型 ID、维度、批量和限流
- [x] 实现 Dense-only 检索 Service，并接入同一评估集脚本
- [ ] 使用真实 Qdrant 和 Qwen 完成 Dense 与词法基线的同集比较
- [ ] 设计 Worker、对账、旧向量清理和 active index 切换

## 14. 决策与变更记录

| 日期 | 决策或变化 | 原因 |
| --- | --- | --- |
| 2026-09-19 | 固定使用 `dense` named vector | 为后续 Sparse 或多种向量预留明确边界 |
| 2026-09-19 | Qdrant payload 不保存完整正文 | MySQL 保持正文唯一事实来源 |
| 2026-09-19 | point ID 使用 UUID5 | 相同版本和内容重复执行时可追踪、可去重 |
| 2026-09-19 | 先同步实现最小闭环 | 先验证模型、维度和存储契约，再引入 Worker |
| 2026-09-19 | 失败时尽力删除本次点 | 降低跨库漂移，但不声称跨库原子事务 |
| 2026-09-19 | 烟测使用随机临时 Collection 并自动清理 | 避免误删或污染正式 Collection |
