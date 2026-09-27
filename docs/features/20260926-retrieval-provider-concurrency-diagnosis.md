# 检索与 Provider 并发边界诊断

> 状态：已实现
> 创建日期：2026-09-26
> 最近更新：2026-09-26
> 关联任务：拆分生成并发评估中观察到的检索与 Provider 延迟变化，建立批量检索和有界并发诊断能力

## 1. 背景与问题

生成评估的 `concurrency=1/4` 对照显示，`c4` 的吞吐提升约 `2.39x`，但单请求平均
延迟、P95 和 TTFT 均上升，其中 `retrieval` P95 从 `1831.997 ms` 增加到
`4676.794 ms`。线上一轮复跑只能证明存在资源竞争，不能区分以下来源：

1. Qdrant、Embedding、MySQL 词法检索还是 Chat Provider 造成延迟变化。
2. 多查询变体是串行搜索、批量搜索还是受共享客户端并发限制。
3. 增加并发是否只提高吞吐，是否会损害单请求延迟或质量。
4. 减少查询变体是否能以可接受的质量代价换取性能。

因此本轮先补齐批量基础设施和隔离诊断，不直接修改在线并发参数。

## 2. 目标

- Qdrant 适配器支持一次提交多个查询请求，并按输入顺序返回结果。
- Dense 检索优先使用批量向量搜索；不支持批量的实现以最大并发 `4` 回退逐条搜索。
- 词法检索支持一次数据库往返完成多个查询变体，并保持每个变体的独立过滤条件。
- Retrieval 评估支持 `--concurrency`，每样本独立创建检索栈，同时共享进程级
  Embedding 和 Qdrant 客户端。
- 建立图级、Provider 级和 Embedding HTTP 级诊断脚本，区分假实现与真实依赖。
- 在冻结的 v3 回归集上比较批量修复、候选池消融、查询变体和 `c1/c4`。
- 保持公开 API、SSE、数据库结构和前端不变。

## 3. 非目标

- 不把评估并发参数接入 FastAPI、SSE 或反向代理配置。
- 不宣称当前 `c4` 结果等于生产容量、SLA 或跨机器性能结论。
- 不通过减少查询变体换取延迟；质量回退不可接受。
- 不做模型型 Rerank、缓存、连接池参数调优或数据库索引调优。
- 不修改在线默认检索策略、Dense 阈值、数据库数据或 Qdrant Collection。
- 不将诊断脚本作为 CI 的真实网络测试。

## 4. 用户场景与验收标准

| 场景 | 操作 | 预期结果 |
| --- | --- | --- |
| 批量 Dense | 对同一查询的多个变体执行 Dense 检索 | 一次 `query_batch_points()`，结果按输入顺序对应 |
| 无批量实现 | 注入仅支持 `search()` 的向量存储 | 最多并发 `4` 次逐条搜索，不改变结果结构 |
| 批量词法 | 对多个查询变体执行词法检索 | 单次数据库往返，每个变体保留自己的过滤条件和 limit |
| 候选预算 | 批量路径传入已经扩大的候选 limit | 不在批量层再次扩大候选池 |
| 检索并发 | 使用 `--concurrency 4` 运行固定数据集 | 同时执行数不超过 4，报告 wall time、吞吐和延迟 |
| 图级隔离 | 假/真检索 × 假/真 Provider 四种组合 | 能识别延迟由哪一侧主导 |
| Provider 隔离 | generate、stream、mixed 三种负载 | 记录吞吐、延迟、TTFT、错误和增量数 |
| Embedding 诊断 | 真实图检索 + 假 Provider | 关联逻辑调用和物理 HTTP 调用，记录重试与并发 |
| 在线服务 | 检查公开 API 和 SSE | 不新增字段，不改变默认策略和并发 |

## 5. 方案概览

### 5.1 向量存储批量检索

新增运行时可检查的 `BatchVectorStorePort`。`DenseRetrievalService` 先批量生成全部
查询向量，再构造与变体一一对应的 `VectorSearchRequest`：

- `QdrantVectorStore` 实现 `search_batch()`，底层调用
  `query_batch_points()`，只进行一次客户端批量调用。
- 返回值数量和输入数量不一致时抛出 `VectorStoreError`，不静默补空结果。
- 不支持批量的向量存储进入 `asyncio.Semaphore(4)` 回退路径，避免无限并发。
- 单条搜索和批量搜索都复用相同的过滤条件、向量名和候选 limit。

### 5.2 词法批量检索

`ChunkRepository.search_lexical_batch()` 为每个请求构造一条候选分支，通过
`UNION ALL` 合并，再用 `ROW_NUMBER()` 按 `query_index` 分区和限制每个变体：

1. 每个变体独立应用作者、朝代、粒度和注释可见性过滤。
2. 结果按 `query_index` 和 `variant_rank` 返回，调用方按输入顺序恢复分组。
3. 批量层不再重复执行候选池放大；扩大预算由 Hybrid 调用方负责。
4. `RetrievalService.search_evidence_batch()` 复用原有打分、排序和裁剪规则。

### 5.3 检索评估并发

`RetrievalEvaluator` 支持 `retrieval_factory` 和 `concurrency`：

- 每个样本通过 factory 建立独立数据库会话和检索对象。
- Embedding Provider 与 Qdrant 客户端在样本之间共享，模拟在线进程的资源复用。
- `asyncio.Semaphore` 限制并发，`asyncio.gather` 保持结果顺序。
- 报告记录并发数、wall time 和吞吐，原质量指标不变。
- 同时传入共享 retrieval 和大并发会在启动前失败，防止误测。

### 5.4 分层诊断

诊断顺序为：

```text
批量检索修复
  -> v3 质量与延迟回归
  -> 查询变体消融
  -> 图级假/真隔离
  -> Chat Provider 独立诊断
  -> Embedding HTTP 调用诊断
```

图级脚本把检索与生成替换为可控假实现，Provider 脚本只调用 Chat Provider，避免把
数据库、Qdrant、Embedding 和 Chat 上游的延迟混在一起。

## 6. 接口与契约

新增内部协议：

```python
@runtime_checkable
class BatchVectorStorePort(Protocol):
    async def search_batch(
        self,
        requests: list[VectorSearchRequest],
    ) -> list[list[VectorSearchHit]]: ...
```

新增内部方法：

```text
ChunkRepository.search_lexical_batch(requests) -> list[list[ChunkSearchCandidate]]
RetrievalService.search_evidence_batch(requests) -> list[RetrievalSearchResult]
QdrantVectorStore.search_batch(requests) -> list[list[VectorSearchHit]]
```

`evaluate_retrieval.py` 新增：

```text
--concurrency INT   Maximum concurrent evaluation cases. Default: 1
```

检索报告新增字段及默认值：

| 层级 | 字段 | 默认值 |
| --- | --- | --- |
| report | `concurrency` | `1` |
| report | `wall_time_ms` | `0.0` |
| report | `throughput_cases_per_second` | `null` |

新增诊断脚本：

```text
apps/api/scripts/profile_graph_concurrency.py
apps/api/scripts/profile_provider_concurrency.py
```

公开 HTTP API、SSE 事件和前端契约不变。批量协议和诊断脚本只属于内部基础设施与
离线评估入口。

## 7. 数据设计

不涉及 MySQL、Alembic、Qdrant Collection 或语料数据变更。批量词法检索复用现有
`poem_chunks` 查询条件和版本可见性规则；诊断报告只以本地 JSON 保存，记录模型、
模式、并发、延迟、错误、逻辑调用和 HTTP 调用元数据，不保存请求正文或密钥。

## 8. 后端设计

| 模块 | 职责 |
| --- | --- |
| `app/ai/providers/vector_store.py` | 定义 `BatchVectorStorePort` |
| `app/ai/providers/qdrant.py` | 实现 Qdrant 批量向量搜索 |
| `app/services/dense_retrieval.py` | 批量构造查询，优先批量搜索并限制回退并发 |
| `app/repositories/chunks.py` | 使用 `UNION ALL + ROW_NUMBER()` 批量词法查询 |
| `app/services/retrieval.py` | 批量复用词法打分和排序 |
| `app/services/hybrid_retrieval.py` | 优先调用批量词法接口 |
| `app/evaluation/retrieval.py` | 有界并发、结果顺序、wall time 和吞吐 |
| `app/schemas/evaluation.py` | 报告并发和性能字段 |
| `apps/api/scripts/evaluate_retrieval.py` | CLI 参数、每样本检索栈和资源生命周期 |
| `apps/api/scripts/profile_graph_concurrency.py` | 图级假/真依赖隔离和 Embedding HTTP 观测 |
| `apps/api/scripts/profile_provider_concurrency.py` | Chat Provider 独立并发诊断 |

失败处理：

1. 批量返回数量不一致直接抛错，不把缺失结果解释为空召回。
2. 单样本检索异常继续由评估器按原语义处理。
3. Provider 诊断不重试，避免把重试成本隐藏为吞吐。
4. 诊断 CLI 不修改 `.env`、Settings 持久值或在线资源。

## 9. 前端设计

不涉及前端改动。

## 10. RAG 与评估

### 10.1 批量检索质量与候选池

v3 回归集、Top-5、`expanded-hybrid-rrf-v1`：

| 报告 | 通过 | Recall@5 | nDCG@5 | MRR | 平均延迟 | P95 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 批量 limit 修复后 `c1` | 45/46 | 1.000000 | 0.947993 | 0.929825 | 436.045 ms | 1149.235 ms |
| 候选池消融 `c1` | 45/46 | 1.000000 | 0.947993 | 0.929825 | 447.135 ms | 1196.559 ms |

候选池消融没有提高质量，也没有降低延迟，已撤回。批量路径只改变执行方式，不替换
在线检索质量策略。

### 10.2 查询变体消融

同一 v3 回归集，逐步减少查询变体：

| 变体上限 | 通过 | Recall@5 | nDCG@5 | MRR | 平均延迟 | P95 |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 8 | 45/46 | 1.000000 | 0.947993 | 0.929825 | 436.045 ms | 1149.235 ms |
| 4 | 44/46 | 0.973684 | 0.921677 | 0.903509 | 331.981 ms | 663.489 ms |
| 2 | 43/46 | 0.947368 | 0.920741 | 0.929825 | 232.926 ms | 384.667 ms |
| 1 | 36/46 | 0.815789 | 0.771020 | 0.759649 | 216.564 ms | 348.324 ms |

结论：减少变体虽然明显降低延迟，但会快速损失召回和排序质量。保留
`CHAT_QUERY_VARIANT_LIMIT=8`，性能优化必须依靠批量化、有界并发或资源预算，而不是
截断查询表达。

### 10.3 检索并发

v3 回归集、相同质量口径：

| 指标 | `c1` | `c4` |
| --- | ---: | ---: |
| 通过 | 45/46 | 45/46 |
| Recall@5 | 1.000000 | 1.000000 |
| nDCG@5 | 0.947993 | 0.947993 |
| MRR | 0.929825 | 0.929825 |
| wall time | 26384.588 ms | 6742.179 ms |
| 吞吐 | 1.743 cases/s | 6.823 cases/s |
| 平均延迟 | 573.375 ms | 528.598 ms |
| P95 延迟 | 1478.885 ms | 1344.406 ms |

在这组本机单次运行中，`c4` 对检索评估只观察到吞吐提升，没有质量或平均延迟回退。
这不是生产容量结论，仍需重复实验、连接池预算和稳定网络条件。

### 10.4 图级隔离

4 个固定样本、各模式各自 `c1/c4`：

| 检索 | Provider | `c1` 吞吐 | `c4` 吞吐 | `c1` 平均 | `c4` 平均 |
| --- | --- | ---: | ---: | ---: | ---: |
| fake | fake | 10.460 req/s | 32.410 req/s | 95.561 ms | 111.258 ms |
| fake | real | 0.598 req/s | 1.750 req/s | 1673.184 ms | 2028.705 ms |
| real | fake | 0.838 req/s | 2.655 req/s | 1193.736 ms | 1161.780 ms |
| real | real | 0.248 req/s | 0.723 req/s | 4026.356 ms | 3998.650 ms |

真实 Chat Provider 是当前图级延迟的主要组成部分；真实检索也带来明显延迟，但在这组
样本的 `c4` 下平均延迟没有继续恶化。四种组合都 `4/4` 成功且无错误。

### 10.5 Provider 独立诊断

| 模式 | `c1` 吞吐 | `c4` 吞吐 | `c1` 平均 | `c4` 平均 |
| --- | ---: | ---: | ---: | ---: |
| generate | 1.492 req/s | 4.233 req/s | 670.359 ms | 836.325 ms |
| mixed | 1.201 req/s | 3.215 req/s | 832.680 ms | 966.893 ms |
| stream | 1.021 req/s | 2.978 req/s | 979.084 ms | 1109.997 ms |

三种模式全部成功且无 Provider 错误。`c4` 吞吐约提升 `2.8x` 至 `2.9x`，但单请求
平均延迟同时上升，说明有界并发改善的是批处理吞吐，不是单个用户的首字或总等待时间。

### 10.6 Embedding 批量与长尾

真实检索 + 假 Provider、26 个请求、`c4`：

| 指标 | 结果 |
| --- | ---: |
| 逻辑 Embedding 调用 | 24 |
| 物理 HTTP 请求 | 24 |
| 最大逻辑并发 | 4 |
| 最大 HTTP 并发 | 4 |
| 平均 HTTP 延迟 | 266.943 ms |
| P95 HTTP 延迟 | 445.109 ms |
| 最大 HTTP 延迟 | 1521.541 ms |
| 重试次数 | 0 |

24 次逻辑调用严格对应 24 次 HTTP 调用，说明本轮没有在相同输入上重复 Embedding。
单次 `1521.541 ms` 的长尾说明平均值不足以代表稳定性，后续需要单独验证网络抖动、
重试和超时预算。

随后使用同一数据集全量 26 个请求、`repeat=4` 共 104 次请求做同配置复跑：

| 指标 | `c1` | `c4` |
| --- | ---: | ---: |
| 成功 | 104/104 | 104/104 |
| wall time | 69601.611 ms | 16938.778 ms |
| 吞吐 | 1.494 req/s | 6.140 req/s |
| 平均请求延迟 | 669.223 ms | 643.098 ms |
| P95 请求延迟 | 1382.033 ms | 1306.759 ms |
| Embedding 逻辑调用 | 96 | 96 |
| Embedding HTTP 请求 | 96 | 96 |
| Embedding 最大并发 | 1 | 4 |
| 平均 HTTP 延迟 | 225.055 ms | 202.665 ms |
| P95 HTTP 延迟 | 297.020 ms | 293.950 ms |
| 最大 HTTP 延迟 | 1513.087 ms | 487.796 ms |
| 重试 / 限流错误 | 0 / 0 | 0 / 0 |

复跑确认 Embedding 网络长尾并非一次偶然，`c1` 再次出现 `1513.087 ms` 的最慢请求；
本次 `c4` 没有出现重试、限流或失败，wall time 和观测到的最大 HTTP 延迟均更低。
这只能说明当前本机与本轮上游状态下 `c4` 可用，不能据此推断长期稳定性或生产容量。

### 10.7 结论

- 保留批量 Qdrant、批量词法和检索并发评估能力。
- 保留 8 个查询变体；不得用减少变体替代性能优化。
- 不启用候选池放大。
- 在线 API、SSE、数据库、Qdrant Collection、前端和默认并发不变。
- 当前并发只提高吞吐，真实 Provider 下单请求延迟仍会恶化。
- 报告只支持当前本地环境，不代表生产容量。

本版纳入 Git 的里程碑报告：

- `data/eval/reports/retrieval_holdout_1000_v3_batch_limit_c1_restored.json`
- `data/eval/reports/retrieval_holdout_1000_v3_performance_c1.json`
- `data/eval/reports/retrieval_holdout_1000_v3_performance_c4.json`
- `data/eval/reports/retrieval_holdout_1000_v3_candidate_pool_c1.json`
- `data/eval/reports/graph_isolation_*_{c1,c4}.json`
- `data/eval/reports/provider_concurrency_*_{c1,c4}.json`
- `data/eval/reports/graph_embedding_v3_real_retrieval_fake_provider_{c1,c4}.json`
- `data/eval/reports/graph_embedding_v3_stability_rerun_{c1,c4}.json`

`slowtail`、smoke、查询变体和其他重复诊断属于本地复现产物，不进入发布基线；
正文中的表格已经保留结论指标，命令可直接重新生成对应文件。

复现命令：

```powershell
.\.venv\Scripts\python.exe apps\api\scripts\evaluate_retrieval.py `
  --dataset data\eval\retrieval_holdout_1000_v3.json `
  --strategy expanded-hybrid --top-k 5 --min-score 0.60 `
  --concurrency 1 `
  --json-output data\eval\reports\retrieval_holdout_1000_v3_performance_c1.json

.\.venv\Scripts\python.exe apps\api\scripts\evaluate_retrieval.py `
  --dataset data\eval\retrieval_holdout_1000_v3.json `
  --strategy expanded-hybrid --top-k 5 --min-score 0.60 `
  --concurrency 4 `
  --json-output data\eval\reports\retrieval_holdout_1000_v3_performance_c4.json

.\.venv\Scripts\python.exe apps\api\scripts\profile_graph_concurrency.py `
  --provider real --retrieval real --concurrency 4 `
  --repeat 1 `
  --json-output data\eval\reports\graph_isolation_real_retrieval_real_provider_c4.json

.\.venv\Scripts\python.exe apps\api\scripts\profile_provider_concurrency.py `
  --mode mixed --concurrency 4 `
  --json-output data\eval\reports\provider_concurrency_mixed_c4.json
```

## 11. 测试计划

| 层级 | 覆盖内容 |
| --- | --- |
| 单元测试 | Qdrant 批量顺序与过滤、Dense 批量优先和回退、词法批量过滤与预算 |
| Repository 测试 | 单次数据库 execute、空请求、非法 limit 和变体隔离 |
| 评估测试 | 并发上限、结果顺序、非法配置、CLI 默认值和旧报告兼容 |
| 诊断测试 | 图级并发上限、Provider 错误隔离、Embedding 重试关联 |
| 真实评估 | v3 检索 `c1/c4`、四种图级组合和三种 Provider 负载 |
| 在线回归 | 公开 API、SSE、数据库、前端与默认策略保持不变 |

实际验证结果：

- 新增或修改的检索、评估和诊断定向测试通过。
- Ruff 通过。
- 真实评估使用 MySQL、Qdrant `poem_chunks_v1`、Qwen `text-embedding-v4` 和
  `deepseek-chat`。
- 提交前执行 `.\scripts\verify.ps1` 和 `git diff --check`。

## 12. 风险与回滚

- 4 个图级样本、4 个 Provider 请求和 46 条检索样本不足以推导生产容量；Embedding
  虽有 104 请求复跑，但仍只覆盖单机、单日上游状态。
- 本机网络、上游限流和单次运行会影响延迟；质量结论比吞吐结论更稳定。
- `search_lexical_batch()` 让单条 SQL 更复杂，候选数和过滤条件必须继续由测试保护。
- Qdrant 批量接口依赖客户端版本，升级时需要重新验证返回顺序和数量。
- 回滚批量路径可恢复逐条检索；回滚评估并发可停止传入 `--concurrency`。
- 删除两个诊断脚本和报告不影响在线运行时，也不涉及数据迁移。

## 13. 实施任务

- [x] 增加 Qdrant 批量搜索和运行时协议
- [x] 增加 Dense 有界回退路径
- [x] 增加词法批量检索并修复候选预算
- [x] 增加 Retrieval 评估并发和报告指标
- [x] 完成查询变体、候选池和批量修复回归
- [x] 完成图级、Provider 级和 Embedding 级诊断
- [x] 同步 README、项目导览、功能索引和开发日志
- [ ] 用重复试验和连接池指标验证稳定性
- [ ] 在质量门槛不变的前提下评估缓存和正式压测工具

## 14. 决策与变更记录

| 日期 | 决策或变化 | 原因 |
| --- | --- | --- |
| 2026-09-26 | 优先批量搜索，不回退减少查询变体 | 变体上限降到 1 只有 36/46 |
| 2026-09-26 | 候选池放大消融后撤回 | 质量不变、延迟无收益 |
| 2026-09-26 | 检索评估并发与在线并发解耦 | 先隔离资源竞争，再决定生产配置 |
| 2026-09-26 | 图级和 Provider 级分开诊断 | 避免把数据库、向量库、Embedding 和 Chat 延迟混在一起 |
| 2026-09-26 | 保留有界并发和长尾观测 | 吞吐提升不能掩盖单请求延迟恶化 |
