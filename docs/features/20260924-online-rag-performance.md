# 在线 RAG 性能优化：资源复用与批量 Embedding

> 状态：已实现
> 创建日期：2026-09-24
> 最近更新：2026-09-24
> 关联任务：复用外部客户端、把查询变体合并为一次 Embedding 调用，并统一检索评估口径

## 1. 背景与问题

可观测性补齐后，阶段耗时显示在线问答的主要可变成本集中在检索阶段的 Embedding
网络往返，而不是 MySQL 或 Qdrant 查询本身。此前存在三个具体问题：

1. 每个问答请求都会新建 Qwen Embedding 客户端和 Qdrant 客户端，流结束时关闭。
   客户端初始化、连接握手和关闭被计入每一次问答的固定成本。
2. 查询扩展会为一次提问生成多个变体（默认上限 `8`）。每个变体单独调用一次
   Embedding，网络往返次数随变体数量线性增长。Profiling 中多证据案例的 Embedding
   累计耗时超过 1.3 秒。
3. `evaluate_retrieval.py` 的 `--min-score` 默认值为 `0.0`，与线上
   `CHAT_DENSE_MIN_SCORE=0.60` 不一致。不带该参数跑出的报告并不代表线上策略，
   容易被误读为质量回退。

## 2. 目标

- 以进程级生命周期复用 Embedding Provider 和 Qdrant 客户端，并在应用退出时释放。
- 一次查询的所有变体合并为一次 Embedding 调用，保持变体执行顺序不变。
- 把跨变体的向量 ID 回查 MySQL 合并为一次查询。
- 让离线检索评估的默认阈值口径与线上一致。
- 保持公开 HTTP、SSE 和数据库契约不变，并在 v2 回归集上确认质量不下降。

## 3. 非目标

- 不引入 Rerank、检索缓存或候选裁剪策略。
- 不修改排序权重、RRF 融合规则、粒度容差或查询改写词典。
- 不做并发 Qdrant 搜索、压测或容量规划。
- 不新增配置项，不引入新的运行时依赖。
- 不做云服务器、域名或 HTTPS 部署。

## 4. 用户场景与验收标准

| 场景 | 操作 | 预期结果 |
| --- | --- | --- |
| 正常问答 | 发送可回答的流式问题 | 公开 SSE 事件与改动前完全一致 |
| 多变体查询 | 问题扩展出多个检索变体 | 只发生一次 Embedding 调用，变体顺序与裁剪结果不变 |
| 批量能力缺失 | 检索分支未实现批量接口 | 逐条回退，结果与批量路径一致 |
| 应用退出 | 服务收到关闭信号 | 共享 Embedding 与 Qdrant 客户端被关闭一次 |
| 检索评估 | 运行 `evaluate_retrieval.py` 不传 `--min-score` | `dense`/`hybrid`/`expanded-hybrid` 使用线上阈值；`lexical`/`expanded` 输出 `not-applicable` |

## 5. 方案概览

### 5.1 资源生命周期

`ChatRetrievalResources` 持有 `embedding_provider` 和 `vector_store` 两个长生命周期
客户端。FastAPI `lifespan` 启动时调用 `create_chat_retrieval_resources()` 创建，
存入 `application.state.chat_retrieval_resources`，并在 `finally` 中关闭。

`get_chat_service` 依赖从 `request.app.state` 读取该对象并注入 `ChatService`，
`build_chat_retrieval` 优先复用共享资源。当共享资源缺失或未配置时，仍然按原逻辑
自建资源，并在本次流结束时通过 `ChatRetrievalStack.aclose()` 关闭，保留原有降级路径。

共享的是无状态 HTTP 客户端与 Qdrant 客户端。`AsyncSession` 属于请求级事务边界，
不进入共享对象。

### 5.2 批量 Embedding 与单次回查

新增内部 `BatchEvidenceRetriever` 协议，声明可选的批量检索能力：

```python
async def search_evidence_batch(
    requests: Sequence[RetrievalRequest],
) -> list[RetrievalSearchResult]: ...
```

实现分三层：

1. `DenseRetrievalService.search_evidence_batch` 把全部变体一次性交给
   `embed_documents()`，校验返回数量和内容后，按变体顺序串行查询 Qdrant，
   再把所有命中的向量 ID 去重，一次调用
   `ChunkRepository.list_public_by_vector_ids()` 回查 MySQL。
2. `HybridRetrievalService.search_evidence_batch` 保持词法分支逐变体执行；Dense
   分支优先走批量能力，未实现批量接口时逐条回退。RRF 融合、降级判定和
   `hard_filtered` 语义不变。
3. `ExpandedRetrievalService` 优先走下游批量能力，否则逐条调用。变体裁剪、
   权重、显式标题候选和结构化候选选择逻辑保持不变。

### 5.3 为什么 Qdrant 搜索仍然串行

每个 Dense 变体都要在同一 `AsyncSession` 上完成可见性回查，而 `AsyncSession` 不能
被并发复用。当前瓶颈是 Embedding 的网络往返，Qdrant 本地查询耗时明显更低，因此
本轮只批量化 Embedding 和 MySQL 回查。并发检索需要先解决 Session 隔离和连接池
预算，属于后续独立验证项。

### 5.4 评估口径统一

`evaluate_retrieval.py` 的 `--min-score` 默认值改为 `None`，由
`resolve_min_score()` 决定实际取值：

- `dense`、`hybrid`、`expanded-hybrid`：未显式传参时读取
  `settings.chat_dense_min_score`（线上默认 `0.60`）。
- `lexical`、`expanded`：不应用 Dense 阈值，控制台输出
  `min_score=not-applicable`。

## 6. 接口与契约

### 6.1 公开接口

不新增或修改公开 HTTP API。SSE 事件仍为：

```text
meta -> retrieval -> delta* -> citation* -> done/error
```

`done` 仍严格保持 `{finish_reason, latency_ms}`，`retrieval` 仍为
`{candidate_count, selected_count, strategy}`。批量检索协议只在进程内部使用，
不进入 OpenAPI。

### 6.2 配置

不新增配置项。相关既有配置：

| 环境变量 | 默认值 | 说明 |
| --- | ---: | --- |
| `CHAT_DENSE_MIN_SCORE` | `0.60` | Dense 余弦相似度下限，也是检索评估的默认阈值 |
| `CHAT_QUERY_VARIANT_LIMIT` | `8` | 单次查询扩展最多执行的检索变体数，决定批量 Embedding 的输入上限 |

### 6.3 服务端日志

沿用可观测性版本的 `Chat stream completed` 日志字段，不新增字段。检索阶段耗时
（`retrieval_ms`）现在包含批量 Embedding 和单次回查的总时间。

## 7. 数据设计

不涉及数据库表、字段和迁移。`list_public_by_vector_ids()` 沿用既有查询，只改变
调用次数：由每变体一次变为每请求一次。

## 8. 后端设计

- `apps/api/app/main.py`：`lifespan` 创建并关闭共享检索资源，写入
  `application.state.chat_retrieval_resources`。
- `apps/api/app/api/deps.py`：`get_chat_service` 从 `request.app.state` 注入共享资源。
- `apps/api/app/services/chat.py`：新增 `ChatRetrievalResources`、
  `create_chat_retrieval_resources()` 和 `ChatRetrievalStack.aclose()`；共享资源
  未配置时保留自建降级路径。
- `apps/api/app/services/retrieval.py`：新增 `BatchEvidenceRetriever` 协议和
  `RetrievalRequest`。
- `apps/api/app/services/dense_retrieval.py`：实现批量 Embedding 和单次向量 ID 回查。
- `apps/api/app/services/hybrid_retrieval.py`：新增批量融合入口，保留逐条回退。
- `apps/api/app/services/query_expansion.py`：优先调用下游批量能力。
- `apps/api/app/repositories/chunks.py`：`list_public_by_vector_ids()` 支持跨变体合并回查。
- `apps/api/scripts/evaluate_retrieval.py`：新增 `expanded-hybrid` 策略和
  `resolve_min_score()`。
- `apps/api/scripts/profile_retrieval.py`：支持记录批量 Embedding 输入数量和 Qdrant
  调用明细。

## 9. 前端设计

不涉及前端改动。前端继续消费既有 SSE 事件，无需感知资源复用或批量 Embedding。

## 10. RAG 与评估

### 10.1 质量

同一 `retrieval-holdout-1000-v2` 回归集、同一 `expanded-hybrid-rrf-v1` 策略、
同一 `CHAT_DENSE_MIN_SCORE=0.60`：

| 指标 | 批量化前 | 批量化后 |
| --- | ---: | ---: |
| 通过 | 45/46 | 45/46 |
| Recall@5 | 1.000000 | 1.000000 |
| MRR | 0.907895 | 0.907895 |
| 无答案准确率 | 0.875 | 0.875 |
| 平均延迟 | 874.575 ms | 483.760 ms |
| P95 | 2770.212 ms | 1239.925 ms |

唯一失败仍是 `no-answer-v2-xinqiji-office-08`，由在线 `assess` 负责拒答。批量化后
平均延迟下降约 44.7%，P95 下降约 55.2%，质量指标完全一致。

报告：

- 批量化前：`data/eval/reports/retrieval_holdout_1000_v2_expanded_hybrid_final.json`
- 批量化后：`data/eval/reports/retrieval_holdout_1000_v2_after_batch_perf_min060.json`

后续第三批独立 holdout v3 复核了同一在线组合：检索 45/46、Recall@5 `1.0`、
MRR `0.929825`，生成 25/26、拒答 `10/10`、引用 P/R `1.0 / 1.0`。完整边界见
[独立 1000 首 holdout v3](20260924-independent-1000-holdout-v3.md)。

不带 `--min-score` 的历史诊断报告
`data/eval/reports/retrieval_holdout_1000_v2_after_batch_perf.json` 为 42/46。它
未启用线上阈值，只用于观察阈值影响，不能作为正式结果引用。

### 10.2 Profiling

`profile_retrieval.py` 的逐变体 Embedding 记录与批量记录对比：

| 案例 | 批量化前总耗时 | 批量化后总耗时 | Embedding 调用 |
| --- | ---: | ---: | --- |
| `multi-v2-changhen-yulin-01` | 4451.233 ms | 1451.410 ms | 18 次逐条 -> 1 次 8 输入 |
| `structured-v2-song-ci-sorrow-02` | 1715.326 ms | 484.000 ms | 9 次逐条 -> 1 次 6 输入 |

批量化后新增记录 `long-v2-changhen-tail-01` 总耗时 2021.648 ms，其中 Embedding
一次 8 输入耗时 432.784 ms、Qdrant 8 次合计 546.926 ms，说明长文本案例的主要成本
仍来自 Qdrant 串行查询和候选规模，而不是 Embedding。

报告：

- 逐变体版本：`data/eval/reports/_probe_retrieval_profile_v2_after_entity_tail.json`
- 批量版本：`data/eval/reports/_probe_retrieval_profile_v2_after_batch.json`

两次 profiling 都是单次运行，同批请求共享批量耗时，属于近似统计，不能当作正式
基准或 A/B 结论。

### 10.3 生成层

真实 `deepseek-chat` 在 `generation-holdout-1000-v2` 上复跑两次：

| 轮次 | 通过 | 拒答 | 引用 P/R | 平均延迟 | P95 |
| --- | ---: | ---: | ---: | ---: | ---: |
| 第一次 | 25/26 | 10/10 | 1.0 / 1.0 | 3768.214 ms | 6856.405 ms |
| 第二次 | 25/26 | 10/10 | 1.0 / 1.0 | 3596.143 ms | 6490.154 ms |

两次失败样本不同（`gen-v2-qingyuan-search` 与 `gen-v2-tianmu-freedom`），答案语义
基本正确，只是未逐字命中评估脚本的严格短语断言。生成延迟由模型主导，本轮没有
观察到与检索批量化对应的稳定变化。

报告：

- `data/eval/reports/generation_holdout_1000_v2_after_batch_perf.json`
- `data/eval/reports/generation_holdout_1000_v2_after_batch_perf_rerun.json`

`data/eval/reports/generation_holdout_1000_v2_after_quote_prompt.json` 对应一轮
已撤回的临时提示词实验，不作为最终报告。

### 10.4 复现命令

```powershell
.\.venv\Scripts\python.exe apps\api\scripts\evaluate_retrieval.py `
  --strategy expanded-hybrid --top-k 5 `
  --json-output data\eval\reports\retrieval_holdout_1000_v2_after_batch_perf_min060.json

.\.venv\Scripts\python.exe apps\api\scripts\profile_retrieval.py `
  --case-id multi-v2-changhen-yulin-01 `
  --json-output data\eval\reports\_probe_retrieval_profile_v2_after_batch.json
```

`evaluate_retrieval.py` 现在默认读取 `CHAT_DENSE_MIN_SCORE`，无需再手工补
`--min-score 0.60`。仍建议在报告说明中显式写出实际阈值，避免与其他环境的结果混用。

## 11. 测试计划

| 层级 | 覆盖内容 |
| --- | --- |
| 单元测试 | 批量 Embedding 的输入数量校验、变体顺序、结果数量不一致时的异常、逐条回退路径 |
| 单元测试 | `resolve_min_score()` 对 Dense 与非 Dense 策略的默认口径 |
| 契约测试 | 公开 SSE 事件集合与 `done` 字段保持严格不变 |
| 集成测试 | `ChatService` 复用注入资源，未注入时自建并关闭 |
| 真实评估 | v2 检索与生成 holdout 复跑，对比质量与延迟 |

实际验证结果：

- 后端全量测试：`179 passed, 3 warnings`。
- Ruff：`All checks passed!`。
- 真实 MySQL、Qdrant、Qwen Embedding 和 `deepseek-chat` 均完成调用。
- 检索质量与延迟对比见 10.1，Profiling 见 10.2，生成波动见 10.3。

## 12. 风险与回滚

- 共享 `QwenEmbeddingProvider` 和 `AsyncQdrantClient` 尚未做跨请求并发负载验证；
  高并发下的连接复用行为需要单独压测。
- `AsyncSession` 不能并行复用，因此 Qdrant 搜索仍按变体串行执行；变体上限是当前
  唯一的扇出控制手段。
- 向量 ID 全量回查在 1000 首规模下可用，数据量增长后需要分块或限制候选数量。
- Profiling 中同批请求共享批量耗时，属于近似统计，不能作为单变体精确耗时。
- 回滚方式：在 `get_chat_service` 中停止注入 `chat_retrieval_resources`，或在
  `build_chat_retrieval` 中忽略 `resources` 参数，即可恢复请求级自建资源；批量
  检索协议可按分支退回逐条调用。回滚不涉及数据库迁移、公开 HTTP 或 SSE 变更。

## 13. 实施任务

- [x] 在应用生命周期内创建和关闭共享检索资源
- [x] 通过依赖注入把共享资源传入 `ChatService`
- [x] 新增批量检索协议并实现 Dense 批量 Embedding
- [x] 合并跨变体的 MySQL 向量 ID 回查
- [x] Hybrid 与 Expanded 层优先使用批量能力并保留回退
- [x] 统一检索评估的 Dense 阈值口径
- [x] 增加单元、契约和集成回归测试
- [x] 在 v2 回归集复跑检索与生成评估并落盘报告
- [x] 同步 README、项目导览、接口契约和开发日志
- [ ] 跨请求并发负载验证与 Qdrant 并发检索方案
- [ ] 向量 ID 回查的分块或候选上限

## 14. 决策与变更记录

| 日期 | 决策或变化 | 原因 |
| --- | --- | --- |
| 2026-09-24 | Embedding 与 Qdrant 客户端改为进程级共享 | 消除每请求初始化和连接握手的固定成本 |
| 2026-09-24 | 只共享无状态客户端，不共享 `AsyncSession` | 保持请求级事务边界和可见性回查语义 |
| 2026-09-24 | 变体查询合并为一次 Embedding 调用 | Embedding 网络往返是当时的主要可变成本 |
| 2026-09-24 | Qdrant 搜索保持串行 | `AsyncSession` 不能并发复用，且 Qdrant 耗时占比更低 |
| 2026-09-24 | 批量路径保留逐条回退 | 不强制所有检索分支实现批量接口，便于增量演进 |
| 2026-09-24 | 评估脚本默认读取线上 Dense 阈值 | 避免不带参数的诊断报告被误读为线上质量回退 |
| 2026-09-24 | 生成层两次 25/26 按随机波动记录，不下因果结论 | 两次失败样本不同，且检索质量指标未变化 |
