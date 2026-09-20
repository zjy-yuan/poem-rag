# 内部 Dense 检索与 MySQL 可见性回查

> 状态：已实现（内部 Service 与离线评估入口）  
> 创建日期：2026-09-19  
> 最近更新：2026-09-19  
> 关联任务：在 Qdrant 最小索引闭环之上实现 `dense-baseline-v1`，并与词法基线使用同一评估集比较

## 1. 背景与问题

项目已经完成：

1. `structural-v1` 版本化切块。
2. Qwen Embedding Provider。
3. chunks -> Embedding -> Qdrant upsert 与 MySQL 映射回写。
4. `lexical-baseline-v1` 证据检索和 27 条固定评估集。

但 Qdrant 目前只能写入，不能参与检索，因此无法回答：

1. 自然语言问题是否能召回到正确的诗句、作品或赏析。
2. Dense 检索相对词法基线改善了哪些分类，破坏了哪些已通过样本。
3. Qdrant 中已删除、已撤回、旧版本或失效注释的向量是否会被错误返回。
4. 查询向量、元数据过滤和数据库可见性校验应该由哪一层负责。

如果直接把 Qdrant 命中结果作为最终证据，会绕过 MySQL 中的发布状态、软删除、
当前版本和注释审核规则，产生“向量库幽灵数据”问题。

## 2. 目标

1. 定义可替换的向量检索端口，隔离 Qdrant SDK 类型。
2. 实现 `dense-baseline-v1` 内部 Service：查询向量 -> Qdrant 召回 -> MySQL 回查。
3. 支持 `granularity`、`author_id`、`dynasty_id` 和 `chunk_strategy` 过滤。
4. 只返回已发布且未软删除作品的当前版本证据。
5. 只返回 `ready` chunk；note chunk 还必须关联已发布注释。
6. 保持 `RetrievalEvidence` 输出结构与现有词法检索一致，便于复用评估器。
7. 让评估脚本支持 `--strategy lexical|dense`，使用同一数据集和指标。
8. 使用 Fake Provider 和 Fake Vector Store 覆盖排序、过滤和错误路径。

## 3. 非目标

1. 不修改公开 `GET /api/v1/search/evidence` 的默认策略。
2. 不实现 Sparse 检索、RRF、Rerank、查询改写或 LangGraph。
3. 不实现旧向量清理、active index 切换或全量跨库对账。
4. 不在普通单元测试中调用真实 Qdrant 或 DashScope。
5. 不把 Qdrant payload 当作正文、权限或版本的事实来源。
6. 不宣称 Dense 已经优于词法基线，除非真实同集评估产生证据。

## 4. 用户场景与验收标准

| 场景 | 操作 | 预期结果 |
| --- | --- | --- |
| 正常召回 | Qdrant 返回两个可见 chunk | 按 Qdrant 分数顺序返回 MySQL 证据 |
| 元数据过滤 | 指定粒度、作者或朝代 | 请求条件同时传给 Qdrant 和 MySQL 回查 |
| 草稿作品 | Qdrant 命中草稿作品向量 | MySQL 回查丢弃 |
| 软删除作品 | Qdrant 命中已删除作品向量 | MySQL 回查丢弃 |
| 旧版本向量 | 作品更新后仍命中旧版本点 | 当前版本校验丢弃 |
| 草稿注释 | note 向量关联未发布注释 | 可见性校验丢弃 |
| 无数据库映射 | Qdrant 返回未知 point ID | 丢弃，不构造证据 |
| 空查询 | 查询文本仅空白 | 返回 `422 VALIDATION_ERROR` |
| Embedding 失败 | Provider 抛出错误 | 返回 `503 EMBEDDING_PROVIDER_ERROR` |
| 向量库失败 | Qdrant 查询抛出错误 | 返回 `503 VECTOR_STORE_ERROR` |
| 离线评估 | 执行 `--strategy dense` | 复用同一评估集和指标计算 |

## 5. 方案概览

```text
query
  -> DenseRetrievalService 参数校验
  -> EmbeddingProvider.embed_query()
  -> VectorStore.search(VectorSearchRequest)
  -> Qdrant query_points(using="dense", filter=...)
  -> ChunkRepository.list_public_by_vector_ids()
  -> MySQL 当前版本、发布状态、chunk 状态和注释可见性校验
  -> RetrievalEvidence[]
```

关键取舍：

1. Qdrant 负责向量相似度召回和粗过滤，不负责最终权限判断。
2. MySQL 继续作为作品、版本、注释和正文的唯一事实来源。
3. Qdrant 命中后按 `vector_id` 批量回查 MySQL，保留 Qdrant 的原始排序。
4. 候选查询数量为 `limit * 5`，并限制在 200 以内，为过滤后仍能返回足够结果留余量。
5. Dense 输出复用 `RetrievalEvidence`，避免评估器和未来问答节点维护两套证据结构。

## 6. 接口与契约

本切片不新增或修改公开 HTTP API。

内部端口：

```python
class VectorStorePort(Protocol):
    @property
    def collection(self) -> str: ...

    async def ensure_collection(self, *, dimension: int) -> None: ...

    async def upsert(self, points: list[VectorPoint]) -> None: ...

    async def delete(self, point_ids: list[str]) -> None: ...

    async def search(
        self,
        request: VectorSearchRequest,
    ) -> list[VectorSearchHit]: ...
```

检索策略：

| 字段 | 值 |
| --- | --- |
| `strategy` | `dense-baseline-v1` |
| `match_types` | `["dense_similarity"]` |
| 排序 | 保持 Qdrant 相似度命中顺序 |
| 分数范围 | 限制到 `[0, 1]`，保留 6 位小数 |

命令行：

```powershell
.\.venv\Scripts\python.exe apps\api\scripts\evaluate_retrieval.py `
  --strategy dense `
  --top-k 5
```

公开接口边界：

1. `/api/v1/search/evidence` 当前仍使用 `lexical-baseline-v1`。
2. Dense 只供内部 Service、测试和离线评估使用。
3. 切换公开策略前必须完成真实 Qdrant、真实 Qwen 和同集指标比较。

## 7. 数据设计

本切片不新增表或迁移。

依赖字段：

| 字段 | 用途 |
| --- | --- |
| `poem_chunks.vector_id` | 将 Qdrant point 映射回 MySQL chunk |
| `poem_chunks.status` | Dense 只接受 `ready` chunk |
| `poem_chunks.chunk_strategy` | 过滤索引策略版本 |
| `poems.status` | 只接受已发布作品 |
| `poems.deleted_at` | 排除软删除作品 |
| `poems.version_no` | 排除旧版本 chunk |
| `poem_annotations.status` | note chunk 只接受已发布注释 |

失效向量策略：

1. Qdrant 返回旧版本、草稿、软删除或失效注释点时，MySQL 回查直接丢弃。
2. 本切片不删除失效点，只保证检索结果不可见。
3. 旧向量清理、对账和 active index 切换属于后续索引生命周期切片。

## 8. 后端设计

新增：

1. `app/services/dense_retrieval.py`：Dense 检索编排。
2. `apps/api/tests/test_dense_retrieval.py`：排序、过滤、版本和错误测试。

修改：

1. `app/ai/providers/vector_store.py`：增加检索请求、命中和端口方法。
2. `app/ai/providers/qdrant.py`：增加 `query_points()` 和过滤器构造。
3. `app/repositories/chunks.py`：增加按 `vector_id` 回查公开 chunk。
4. `apps/api/scripts/evaluate_retrieval.py`：增加 `--strategy lexical|dense`。

错误映射：

| 异常 | HTTP 错误码 | 处理 |
| --- | --- | --- |
| 空查询 | `422 VALIDATION_ERROR` | 不调用模型和向量库 |
| `EmbeddingProviderError` | `503 EMBEDDING_PROVIDER_ERROR` | 不查询向量库 |
| `VectorStoreError` | `503 VECTOR_STORE_ERROR` | 不返回未经验证的点 |

## 9. 前端设计

本切片不涉及前端，不修改搜索页面和 API 类型。

前端继续调用现有词法证据接口。后续只有公开策略切换、响应字段变化或新增调试信息时，
才需要同步更新契约和前端类型。

## 10. RAG 与评估

评估器继续复用同一 27 条 `lexical-baseline-seed-v1` 数据集和以下指标：

1. Recall@k。
2. MRR。
3. Hit Rate@k。
4. 拒答准确率。
5. 有答案样本无结果率。
6. 平均延迟和 P95 延迟。

当前限制：

1. 真实 Qdrant 适配器烟测已通过，但尚未生成真实 Qwen 查询向量。
2. 真实 chunks 当前仍为 `pending`，需要完成真实索引后才能评估 Dense。
3. 只有真实执行同集评估后，才能记录 Dense 与词法基线的差异。
4. 当前 Dense 只代表基础语义召回，不代表混合检索或最终问答质量。

## 11. 测试计划

| 层级 | 覆盖内容 |
| --- | --- |
| 单元测试 | Qdrant query、named vector、过滤器、空过滤和异常包装 |
| 集成测试 | Fake Provider + Fake Vector Store + SQLite 的证据排序与过滤 |
| 失败测试 | 空查询、Embedding 失败、向量库失败和失效数据丢弃 |
| 契约测试 | Dense Service 输出继续满足 `RetrievalEvidence` |
| E2E | Qdrant 烟测已通过；等待 Qwen 和已索引语料 |
| 评估 | 等待真实 Qwen 环境执行 Dense 与词法同集对比 |

验证结果：

1. Dense 与 Qdrant 专项测试：`15 passed`。
2. 后端完整测试：`72 passed`，有 3 条第三方弃用或连接警告。
3. Ruff：通过。
4. mypy：AI、Dense Service、chunk 仓储和评估脚本通过。
5. 真实 Qdrant 已完成适配器烟测；DashScope/Qwen 尚未完成真实联调。

## 12. 风险与回滚

1. Qdrant 与 MySQL 之间可能存在失效向量，当前通过回查保证不可见，但会增加查询开销。
2. 候选放大倍数可能在高过滤场景下不足，也可能在无过滤场景下增加数据库回查成本。
3. 余弦分数直接映射到 `[0, 1]` 只适合当前 `Cosine` 配置；切换距离函数必须重审。
4. Dense 对短文本、文言文和意象的召回效果尚未验证。
5. 真实模型调用会增加延迟、Token 成本和限流风险。
6. 回滚方式是停止使用 `DenseRetrievalService` 和 `--strategy dense`，公开接口继续使用词法策略。

## 13. 实施任务

- [x] 定义向量检索请求、命中和端口方法
- [x] 实现 Qdrant `query_points()` 与过滤条件
- [x] 实现 Dense Service 和 MySQL 可见性回查
- [x] 增加草稿、软删除、旧版本、注释和缺失映射测试
- [x] 扩展离线评估脚本的 `--strategy dense`
- [x] 回写功能索引、项目说明、契约、README 和开发日志
- [x] 启动真实 Qdrant 并完成 query 烟测
- [ ] 用真实 Qwen Embedding 完成语料索引
- [ ] 记录 Dense 与 `lexical-baseline-v1` 的同集指标和失败样例
- [ ] 根据评估结果设计查询改写、Sparse、RRF、Rerank 和公开策略切换
- [ ] 设计旧向量清理、active index 切换和跨库对账

## 14. 决策与变更记录

| 日期 | 决策或变化 | 原因 |
| --- | --- | --- |
| 2026-09-19 | Qdrant 只负责召回，MySQL 负责最终可见性 | 避免向量库成为权限、版本和正文的第二事实来源 |
| 2026-09-19 | Dense 输出复用 `RetrievalEvidence` | 复用评估器，并为后续问答引用保持统一结构 |
| 2026-09-19 | 暂不切换公开 HTTP 策略 | 先完成真实联调和同集评估，再决定是否替换词法基线 |
| 2026-09-19 | 先实现 Dense-only，不引入混合检索 | 保留可解释对照，避免同时改变多个变量 |
