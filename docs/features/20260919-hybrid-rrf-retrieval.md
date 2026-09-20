# 内部 Hybrid RRF 检索

> 状态：已实现（内部 Service 与离线评估入口）  
> 创建日期：2026-09-19  
> 最近更新：2026-09-19  
> 关联任务：融合 `lexical-baseline-v1` 与 `dense-baseline-v1`，建立可复现的混合召回对照组

## 1. 背景与问题

项目已经有两条独立检索路径：

1. `lexical-baseline-v1` 擅长精确引用、短语、标题、作者和朝代等结构化查询。
2. `dense-baseline-v1` 用于验证自然语言到诗句语义的召回能力。

单一路径都有明显边界。词法基线在三条自然语言样本上全部失败；Dense 尚未完成真实
Qdrant 和 Qwen 联调，也不能假设它会同时保住精确引用。直接把两路分数相加也不可靠：
词法分数是规则得分，Dense 分数来自向量相似度，两者不在同一概率尺度上。

因此需要先建立不依赖分数校准的融合基线，并用固定评估集比较：

1. Hybrid 是否能在不破坏精确类样本的前提下补充语义召回。
2. 同一个 chunk 被两路命中时，排名是否稳定上移。
3. 融合策略的额外延迟和依赖失败是否可解释。
4. 融合结果是否继续满足现有 `RetrievalEvidence` 契约。

## 2. 目标

1. 定义 `hybrid-rrf-v1` 内部检索 Service。
2. 顺序调用现有词法和 Dense 分支，复用两者的 MySQL 可见性规则。
3. 使用 Reciprocal Rank Fusion 按排名融合，不直接比较异构原始分数。
4. 同一 `chunk_id` 只输出一次，保留两路排名、原始分数和匹配类型。
5. 将 RRF 分数归一化到 `[0, 1]`，保持现有证据 Schema 不变。
6. 让离线评估脚本支持 `--strategy hybrid`。
7. 用 Fake 分支覆盖双路命中、单路命中、去重、稳定排序、过滤透传和失败闭锁。

## 3. 非目标

1. 不修改公开 `GET /api/v1/search/evidence` 的默认策略。
2. 不引入 Sparse/BM25、Cross-Encoder Rerank 或查询改写。
3. 不并行调用共享同一个 `AsyncSession` 的两个分支。
4. 不把 RRF 分数解释为概率或最终相关性。
5. 不在真实 Qdrant、Qwen 和同集评估完成前宣称 Hybrid 优于任一基线。
6. 不实现权重学习、动态路由或按查询类型自动选择策略。

## 4. 用户场景与验收标准

| 场景 | 操作 | 预期结果 |
| --- | --- | --- |
| 双路命中 | 同一 chunk 同时进入两路结果 | 只输出一次，RRF 分数高于单路命中 |
| 单路命中 | 仅词法或仅 Dense 返回 chunk | 保留该路结果并标记来源 |
| 同分排序 | 两个候选 RRF 分数相同 | 按最佳源排名、再按 `chunk_id` 稳定排序 |
| 过滤透传 | 请求粒度、作者或朝代过滤 | 原样传给两个分支 |
| 空查询 | 查询文本仅空白 | 返回 `422 VALIDATION_ERROR`，不调用分支 |
| 分支失败 | Dense 或词法抛出错误 | 不静默降级，向调用方暴露原错误 |
| 离线评估 | 执行 `--strategy hybrid` | 复用同一评估集和指标计算 |
| 契约兼容 | Hybrid 输出证据 | 继续满足 `RetrievalEvidence`，分数在 `[0, 1]` |

## 5. 方案概览

```text
query
  -> 参数校验
  -> RetrievalService.search_evidence()       # lexical branch
  -> DenseRetrievalService.search_evidence()  # dense branch
  -> 按 chunk_id 合并
  -> RRF: score += 1 / (60 + rank)
  -> 按 RRF 分数、最佳源排名、chunk_id 排序
  -> 分数归一化并返回 RetrievalEvidence[]
```

关键取舍：

1. 两路各取 `limit * 5`、最多 200 个候选，为融合和过滤后截断留余量。
2. 分支顺序执行，因为两者都使用同一个 `AsyncSession`；不能为了表面低延迟并发调用。
3. RRF 只依赖排名，避免直接相加规则分数和余弦相似度。
4. 两路都排第一的理论上限为 `2 / (60 + 1)`，输出按该上限归一化。
5. 任一分支失败时不返回部分结果，防止评估把依赖故障误判成 Hybrid 效果下降。
6. 证据正文、作者、朝代、版本和可见性仍由两个分支的底层查询保证。

## 6. 接口与契约

本切片不新增或修改公开 HTTP API。

内部策略：

| 字段 | 值 |
| --- | --- |
| `strategy` | `hybrid-rrf-v1` |
| `rank_constant` | `60` |
| `match_types` | 合并来源匹配类型，并追加 `lexical_match`、`dense_similarity`、`rrf_fusion` |
| 排序 | RRF 分数降序、最佳源排名升序、`chunk_id` 升序 |
| 分数范围 | 归一化到 `[0, 1]`，保留 6 位小数 |

命令行：

```powershell
.\.venv\Scripts\python.exe apps\api\scripts\evaluate_retrieval.py `
  --strategy hybrid `
  --top-k 5
```

公开接口边界：

1. `/api/v1/search/evidence` 当前仍使用 `lexical-baseline-v1`。
2. Hybrid 只供内部 Service、测试和离线评估使用。
3. 切换公开策略前必须完成真实 Qdrant、真实 Qwen 和三条策略的同集对比。

## 7. 数据设计

本切片不新增表或迁移。

融合键为 `chunk_id`。第一版不输出 RRF 原始分数、各路排名或权重配置，因为这些字段
会改变公开证据 Schema；在实验阶段通过内部对象和评估报告保留可解释性。

## 8. 后端设计

新增：

1. `app/services/hybrid_retrieval.py`：RRF 融合、排序和证据序列化。
2. `apps/api/tests/test_hybrid_retrieval.py`：融合、去重、排序和失败测试。

修改：

1. `apps/api/scripts/evaluate_retrieval.py`：增加 `--strategy hybrid`。

错误策略：

1. 空查询在调用分支前返回 `422 VALIDATION_ERROR`。
2. 词法分支或 Dense 分支的 `AppError` 原样向上传播。
3. 第一版没有部分降级；如需降级，必须作为独立策略并记录来源。

## 9. 前端设计

本切片不涉及前端，不修改搜索页面和 API 类型。

## 10. RAG 与评估

Hybrid 继续使用 27 条 `lexical-baseline-seed-v1` 数据集，并与以下基线比较：

1. `lexical-baseline-v1`：Recall@5 `0.869565`，MRR `0.869565`。
2. `dense-baseline-v1`：等待真实 Qwen 索引。
3. `hybrid-rrf-v1`：等待真实环境执行。

评估关注点：

1. 自然语言样本是否改善。
2. 精确引用、短语、标题、作者和朝代样本是否保持通过。
3. 无答案样本是否出现错误召回。
4. 平均延迟和 P95 相对单路增加多少。
5. 失败样例来自词法、Dense 还是融合排序。

当前限制：

1. 真实 Qdrant 烟测已通过，尚未生成真实 Qwen 查询向量。
2. 真实 chunks 仍为 `pending`，尚未完成真实索引。
3. 只有真实执行同集评估后，才能记录 Hybrid 与两条基线的差异。

## 11. 测试计划

| 层级 | 覆盖内容 |
| --- | --- |
| 单元测试 | RRF 计分、双路去重、单路命中、稳定排序、匹配类型和分数边界 |
| 集成测试 | 通过评估脚本注入真实检索分支；真实环境等待 Qdrant/Qwen |
| 失败测试 | 空查询、词法失败和 Dense 失败 |
| 契约测试 | Hybrid 输出继续满足 `RetrievalEvidence` |
| E2E | Qdrant 烟测已通过；等待 Qwen 和已索引语料 |
| 评估 | 等待真实 Qwen 环境执行 lexical、dense、hybrid 同集对比 |

验证结果：

1. Hybrid 专项测试：`5 passed`。
2. 后端完整测试：`77 passed`，有 3 条第三方弃用或连接警告。
3. Ruff：通过。
4. mypy：AI、词法检索、Dense、Hybrid、chunk 仓储和评估脚本通过。
5. 真实 Qdrant 已完成适配器烟测；DashScope/Qwen 尚未完成真实联调。

## 12. 风险与回滚

1. 顺序调用两路会增加延迟，尤其真实 Embedding 和 Qdrant 网络调用尚未测量。
2. 第一版固定 `rank_constant=60` 和等权融合，没有按语料或查询类型调参。
3. RRF 归一化上限只适用于当前两路、每路第一名加权的假设；增加分支或权重后必须重审。
4. 分支失败会直接失败，用户体验可能比词法单路差；正式切换前需要超时、降级和监控。
5. 真实语料扩大后，同一作品可能有多个粒度或版本候选，需要继续观察融合排序。
6. 回滚方式是停止使用 `HybridRetrievalService` 和 `--strategy hybrid`，公开接口继续使用词法策略。

## 13. 实施任务

- [x] 定义 Hybrid Service 与 `hybrid-rrf-v1`
- [x] 实现 RRF 融合、去重、来源标记和分数归一化
- [x] 增加双路、单路、排序、过滤、空查询和失败测试
- [x] 扩展离线评估脚本的 `--strategy hybrid`
- [x] 回写功能索引、项目说明、契约、README 和开发日志
- [x] 启动真实 Qdrant 并完成 query 烟测
- [ ] 用真实 Qwen Embedding 完成语料索引
- [ ] 记录 lexical、dense、hybrid 的同集指标和失败样例
- [ ] 根据评估结果决定 Sparse、Rerank、查询改写和公开策略切换

## 14. 决策与变更记录

| 日期 | 决策或变化 | 原因 |
| --- | --- | --- |
| 2026-09-19 | 使用 RRF，不直接相加异构原始分数 | 规则分数与向量相似度不在同一尺度 |
| 2026-09-19 | 两路顺序执行 | 共享同一个 `AsyncSession`，并发调用不安全 |
| 2026-09-19 | 任一分支失败即整体失败 | 避免把依赖故障误判为融合效果 |
| 2026-09-19 | RRF 分数归一化到 `[0, 1]` | 保持现有 `RetrievalEvidence` 契约 |
| 2026-09-19 | 暂不切换公开 HTTP 策略 | 先完成真实联调和同集评估，再决定是否替换词法基线 |
