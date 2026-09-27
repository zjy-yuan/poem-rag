# Embedding 精确缓存

> 状态：已实现
> 创建日期：2026-09-26
> 最近更新：2026-09-26
> 关联任务：在检索与 Provider 并发诊断后，验证可安全回滚的 Embedding 上游调用削减方案

## 1. 背景与问题

在线问答会把一个问题扩展成最多 8 个查询变体，再把这些变体合并为一次 Embedding
批量请求。当前每次请求都会访问 Qwen Embedding，重复问题、重复变体和跨请求的常见
查询无法复用结果；并发诊断中也观察到 Embedding 存在超过 `1.5 s` 的长尾。

直接把检索结果或生成的回答放进 Redis 会引入语料版本、权限、过滤条件、失效和回答
质量校验问题。因此本轮先只处理边界最小、结果确定的 Embedding 精确缓存。

## 2. 目标

- 对完全相同的文本、模型、配置维度和用途复用同一个 Embedding 结果。
- 一次批量请求内部先去重，再对 Redis 执行批量读取和批量写入。
- Redis 未配置、超时、连接失败或缓存内容损坏时 fail-open，不改变真实 Provider
  的结果和异常语义。
- 缓存键只保存哈希，不把原文写入 Redis key、日志或报告。
- 默认关闭，不在本阶段修改在线默认行为。
- 暴露命中、未命中、写入和错误计数，供离线诊断比较收益。

## 3. 非目标

- 不缓存检索候选、问答回答、Prompt、Chat Provider 响应或最终用户会话。
- 不做语义近似命中，不使用向量相似度判断文本是否相同。
- 不缓存上游错误、空向量或维度不符的响应。
- 不在索引导入和 chunk 重建流程启用缓存。
- 不修改公开 HTTP API、SSE、MySQL、Qdrant Collection 和前端。
- 不在本阶段决定生产 TTL 或声明 Redis 缓存的生产容量收益。

## 4. 用户场景与验收标准

| 场景 | 操作 | 预期结果 |
| --- | --- | --- |
| 批内去重 | 同一批输入包含重复文本 | 重复文本只 Embedding 一次，输出仍按原始输入顺序和数量返回 |
| 跨请求命中 | 第二次请求包含已缓存文本 | 不再调用真实 Provider，直接返回缓存向量 |
| 用途隔离 | 同一文本分别作为 document 和 query | 使用不同缓存键，不混用两种向量语义 |
| 配置隔离 | 模型或配置维度变化 | 缓存键变化，不复用旧配置向量 |
| 缓存损坏 | Redis 值不是合法向量 | 按 miss 处理，调用 Provider 并覆盖该值 |
| Redis 故障 | 读取或写入抛出连接异常 | 真实 Provider 正常返回，错误只计数并记录警告 |
| 空批量 | `embed_documents([])` | 不访问 Redis 或 Provider，直接返回空列表 |
| 默认开关 | 未显式开启缓存 | 在线资源使用原始 Provider，行为与当前版本一致 |

## 5. 方案概览

在 Provider 外层增加组合式 `CachedEmbeddingProvider`：

```text
DenseRetrievalService
  -> CachedEmbeddingProvider
       -> RedisEmbeddingCache
       -> QwenEmbeddingProvider
```

`CachedEmbeddingProvider` 只负责键构造、批内去重、命中合并、miss 回填和 fail-open。
Redis 协议通过独立 `EmbeddingCacheStore` 注入，测试不需要真实 Redis 服务。

缓存值只保存 Embedding 向量 JSON；缓存键由规范化 JSON 的 SHA-256 生成，输入包含：

1. 缓存格式版本。
2. `documents` 或 `query` 用途。
3. Provider 模型 ID。
4. Provider 配置维度，允许为 `null`。
5. 原始文本，不做大小写、空白或标点归一化。

文本必须保持原样参与哈希，避免把语义不同的输入错误合并为同一缓存项。

## 6. 接口与契约

新增内部协议：

```python
class EmbeddingCacheStore(Protocol):
    async def get_many(self, keys: list[str]) -> list[str | None]: ...
    async def set_many(self, values: dict[str, str], *, ttl_seconds: int) -> None: ...
    async def aclose(self) -> None: ...
```

新增内部实现：

```text
CachedEmbeddingProvider
RedisEmbeddingCache
```

新增 Settings：

| 配置 | 默认值 | 约束 | 说明 |
| --- | ---: | --- | --- |
| `EMBEDDING_CACHE_ENABLED` | `false` | bool | 只有显式开启才包装在线 Provider |
| `EMBEDDING_CACHE_TTL_SECONDS` | `3600` | `1-604800` | 缓存有效期 |
| `EMBEDDING_CACHE_TIMEOUT_SECONDS` | `0.5` | `0.05-10` | Redis 连接和读写超时 |

公开 HTTP API、SSE 事件和前端契约不变。

## 7. 数据设计

不涉及 MySQL、Alembic、Qdrant Collection 或语料数据变更。Redis key 示例：

```text
poem-rag:embedding:v1:documents:<sha256>
poem-rag:embedding:v1:query:<sha256>
```

Redis value 只包含向量和缓存格式版本，不包含原文、用户 ID、会话 ID、问题或回答。
缓存是可重建数据，允许直接通过 TTL 或删除 key 失效，不作为事实来源。

## 8. 后端设计

| 模块 | 职责 |
| --- | --- |
| `app/ai/providers/embedding_cache.py` | Redis 适配器、组合 Provider、键构造和缓存统计 |
| `app/services/chat.py` | 仅在开关开启且 Redis 已配置时包装共享 Embedding Provider |
| `app/core/config.py` | 缓存开关、TTL 和超时配置 |
| `apps/api/scripts/profile_graph_concurrency.py` | 可选开启缓存，并在离线报告中记录命中指标 |

失败处理：

1. 缓存读取或写入异常只记录 warning，并增加 `errors` 计数。
2. 缓存值解析失败、向量为空、包含非有限数值或维度不符时按 miss 处理。
3. Provider 异常保持原样向上抛出，不把失败结果写入缓存。
4. `asyncio.CancelledError` 不吞掉，取消语义保持正常。
5. 不实现分布式 single-flight；并发 miss 可能重复调用 Provider，但不会破坏正确性。

## 9. 前端设计

不涉及前端改动。

## 10. RAG 与评估

先验证缓存正确性和离线收益，不直接开启在线默认值。离线诊断复用
`profile_graph_concurrency.py`：

```powershell
.\.venv\Scripts\python.exe apps\api\scripts\profile_graph_concurrency.py `
  --provider fake --retrieval real --repeat 4 `
  --embedding-cache `
  --json-output data\eval\reports\graph_embedding_cache_c1.json
```

报告至少记录逻辑 Embedding 调用、物理 HTTP 请求、缓存命中、未命中、写入、错误和
命中率。是否在线启用需要比较：

1. 首次 miss 请求不能出现不可接受的额外延迟。
2. 重复请求命中后物理 HTTP 请求数下降。
3. Provider 不可用或 Redis 故障时仍能完成真实 Provider 调用。
4. Redis value 体积、TTL 和淘汰策略不会影响核心服务。

离线验证使用真实 MySQL、Qdrant 和 Qwen Embedding，生成 Provider 使用 fake，避免把
Chat 延迟混入缓存结果：

| 场景 | 请求成功 | Cache hits | Misses | Writes | Errors | 逻辑调用 | HTTP 调用 | Hit rate |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 冷缓存，`repeat=4` | 104/104 | 453 | 135 | 135 | 0 | 96 | 24 | 0.770408 |
| 热缓存，同进程复跑 | 104/104 | 588 | 0 | 0 | 0 | 96 | 0 | 1.000000 |

冷缓存命中来自同一次查询内重复查询变体、重复文本和跨 case 复用；剩余 miss 仍按用途、
模型和配置维度分别访问真实 Provider。热缓存复跑时 Redis 中已有全部 24 个 Embedding
HTTP 批量结果，因此物理 HTTP 调用降为 `0`，质量请求仍为 `104/104` 成功。

随后对真实 Redis 做只读容量检查：

| 指标 | 当前值 |
| --- | ---: |
| 项目键数量 | 135 |
| 项目键总占用 | 3,335,040 bytes |
| 平均单键占用 | 24,704 bytes |
| 无 TTL 的键 | 0 |
| 剩余 TTL 范围 | 3,095,704-3,133,741 ms |
| `maxmemory` | 0 |
| `maxmemory-policy` | `noeviction` |

该实例可以完成功能验证，但当前没有内存上限，且采用 `noeviction`，不适合直接作为
生产缓存运行。启用在线缓存前，应使用独立 Redis 实例并设置 `maxmemory`；专用实例可
使用 `allkeys-lru`，若与持久键共享实例则使用 `volatile-lru`，同时依赖当前已有 TTL
保证缓存可淘汰。本阶段不修改共享 Redis 的运行配置，也不开启在线缓存。

本版纳入 Git 的里程碑报告：

- `data/eval/reports/graph_embedding_cache_c1.json`
- `data/eval/reports/graph_embedding_cache_c1_warm.json`

报告只记录运行指标、案例标识和 Embedding 调用观测，不包含缓存原文、Redis key、
密钥或用户凭证。

## 11. 测试计划

| 层级 | 覆盖内容 |
| --- | --- |
| 单元测试 | 批内去重、顺序恢复、跨调用命中、用途隔离、模型/维度隔离 |
| 失败测试 | Redis 读写异常 fail-open、损坏向量 miss、Provider 异常不写缓存 |
| 契约测试 | 空批量跳过 I/O、Settings 边界、公开 API 无变化 |
| 离线评估 | 真实检索 + 假 Provider，比较无缓存与开启缓存的 HTTP 调用和延迟 |
| 在线回归 | 默认关闭时行为不变；开启后检索和生成质量不变 |

## 12. 风险与回滚

- 缓存命中会跳过 Provider，键设计错误可能返回错误向量；因此不对文本做归一并保护
  模型、维度和用途维度。
- Redis 不可用时增加一次缓存尝试；超时必须短，且所有失败 fail-open。
- 并发 miss 没有 single-flight，会产生重复上游调用，但不会造成数据错误。
- JSON 向量值体积大于二进制编码；当前规模先验证收益，不提前优化存储格式。
- 默认关闭时删除 wrapper 接入即可回滚；开启后可通过
  `EMBEDDING_CACHE_ENABLED=false` 立即恢复原链路，不需要迁移。

## 13. 实施任务

- [x] 冻结缓存范围、键和失败语义
- [x] 实现 Redis 适配器和组合 Provider
- [x] 接入可选 Settings 与在线资源构造
- [x] 增加单元测试和离线诊断指标
- [x] 完成真实 Redis 收益验证
- [x] 更新项目导览、功能索引和开发日志

## 14. 决策与变更记录

| 日期 | 决策或变化 | 原因 |
| --- | --- | --- |
| 2026-09-26 | 只缓存 Embedding，不缓存检索答案和生成结果 | 精确缓存边界清晰，可安全 fail-open |
| 2026-09-26 | 使用原始文本哈希，不做文本归一化 | 避免把语义不同输入合并 |
| 2026-09-26 | 默认关闭并保留离线开关 | 先验证收益、体积和故障行为 |
| 2026-09-26 | 不在首版实现跨请求 single-flight | 先控制实现复杂度，重复 miss 不影响正确性 |
| 2026-09-26 | Redis 初始化失败时回退原始 Provider | 缓存故障不能连带关闭向量检索 |
| 2026-09-26 | 暂不修改共享 Redis 的 `maxmemory` 与淘汰策略 | 当前实例承担多个本地服务，先完成容量评估再隔离部署 |
