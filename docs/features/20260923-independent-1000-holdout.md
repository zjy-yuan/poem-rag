# 独立 1000 首检索与生成 holdout

> 状态：已实现（加权 RRF v2 当前在线快照，原 holdout 已转为回归集）
> 创建日期：2026-09-23
> 最近更新：2026-09-23
> 关联任务：用独立于旧 50/28 条调参集的 holdout 复核检索组合，并在证据成立后切换在线策略

## 1. 背景与问题

扩库到 1000 首后，旧 50 条检索集和 28 条生成集已经参与多轮修复与调参，只能作为
历史回归，不能继续证明泛化能力。原在线检索为 `expanded-lexical-v1`，Dense 与
Hybrid 只存在于内部 Service 和离线脚本中；如果直接根据旧集指标切换，会把同一批
观察样本同时用于选择和验证。

本切片解决三个问题：

1. 建立与旧调参样本不重叠的检索和生成 holdout。
2. 在同一候选池、同一 Top-5、同一真实模型环境中比较词法扩展、Dense 和融合策略。
3. 在 holdout 结果支持时切换在线检索，同时保留基础设施故障降级和可回滚路径。

## 2. 目标

1. 新增 `retrieval-holdout-1000-v1`，覆盖精确引用、短语、标题、作者、朝代、多证据、
   自然语言、长文本和三类无答案。
2. 新增 `generation-holdout-1000-v1`，覆盖有答案生成、多证据、典故和稳定拒答。
3. 比较以下三条检索路径：
   - `expanded-lexical-v1`
   - `hybrid-rrf-v1`
   - `expanded-lexical-v1 + Dense + RRF`
4. 把证据充分的在线组合接入 `RagChatGraph`，同时保持公开 HTTP 检索接口不变。
5. 记录降级条件、回滚方式和仍然失败的样例，不通过金标准特例制造全通过。

## 3. 非目标

1. 不把 50/28 条 holdout 表述为统计显著或生产泛化结果。
2. 不为唯一失败样例增加作品级特例，也不继续用旧调参集搜索阈值。
3. 不实现 Rerank、结构化作者/朝代/体裁过滤或 Embedding 维度升级。
4. 不修改公开 `GET /api/v1/search/evidence` 的策略和参数。
5. 不修改数据库表结构或 SSE 事件协议。

## 4. 数据集与真实环境

| 项目 | 值 |
| --- | --- |
| 检索数据集 | `data/eval/retrieval_holdout_1000_v1.json` |
| 检索数据集版本 | `retrieval-holdout-1000-v1` |
| 检索样本 | 50 条，其中 42 条有答案、8 条无答案 |
| 生成数据集 | `data/eval/generation_holdout_1000_v1.json` |
| 生成数据集版本 | `generation-holdout-1000-v1` |
| 生成样本 | 28 条，其中 21 条有答案、7 条拒答 |
| 语料 | 1000 首固定来源分层语料，真实库共 1008 首已发布作品、12415 个 chunks |
| 向量模型 | Qwen `text-embedding-v4`，实际 1024 维 |
| 向量库 | Qdrant `poem_chunks_v1`，12415 points |
| 问答模型 | `deepseek-chat` |

检索 holdout 的类别覆盖精确引用、短语、标题、作者、朝代、多证据、自然语言、长文本、
跨域无答案、领域内缺实体和领域内缺属性。生成 holdout 覆盖作品事实、自然语言、
长文本、典故、多证据和三类拒答。

## 5. 方案概览

在线检索由 `build_chat_retrieval()` 组合：

```text
ExpandedRetrievalService
        +
DenseRetrievalService（Qdrant + Qwen，min_score=0.60）
        |
        v
HybridRetrievalService（RRF 融合）
        |
        v
PoemContextRetrievalService（父级上下文补全）
        |
        v
LangGraph rewrite -> retrieve -> assess -> generate|refuse -> validate
```

构造阶段如果缺少 `QDRANT_URL`、DashScope Key，或 Embedding/Qdrant 初始化失败，直接
使用 `expanded-lexical-v1`。运行期 Hybrid 只对以下两类基础设施错误降级：

- `EMBEDDING_PROVIDER_ERROR`
- `VECTOR_STORE_ERROR`

其他异常继续抛出，避免把程序错误或数据错误静默伪装成检索结果变差。

## 6. 检索结果

三条策略在同一 `retrieval-holdout-1000-v1`、同一 Top-5、真实 MySQL + Qdrant +
Qwen 环境中运行：

| 策略 | 通过 | Recall@5 | MRR | 无答案准确率 | 平均延迟 | P95 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| `expanded-lexical-v1` | 48/50 | 0.952381 | 0.880952 | 1.0 | 399.557 ms | 1082.055 ms |
| `hybrid-rrf-v1` | 40/50 | 0.869048 | 0.735714 | 0.5 | 273.208 ms | 431.425 ms |
| `expanded-lexical-v1 + Dense + RRF` | 50/50 | 1.000000 | 0.887698 | 1.0 | 1006.362 ms | 2944.838 ms |

加权 RRF、显式标题完整作品槽位和 `white_hair_exaggeration` 受控概念规则完成后，
在线组合在同一批 50 条样本上达到 `50/50`，Recall@5 `1.0`，MRR `0.887698`，无答案
准确率保持 `1.0`。修复的是通用检索行为，而不是按样本 ID 或金标准作品 ID 增加分支：

- 查询权重区分原始查询、显式标题、作者实体、概念扩展和长/短内容词。
- 单个显式标题命中的作品会保留一个 poem 粒度证据槽位，避免被笔记 chunk 挤满。
- `所见` 等自然语言疑问词加入停用词，`白发三千丈` 作为可审查的领域概念规则进入
  版本化词典。

性能成本不能忽略：在线组合平均延迟从上一版 `489.661 ms` 增至 `1006.362 ms`，
P95 从 `1198.665 ms` 增至 `2944.838 ms`。当前更适合本地演示和功能验证，不能据此
宣称生产级性能；并发压测、检索并行化和候选裁剪必须在云部署前完成。

当前没有检索失败样例。由于这 50 条样本已经用于诊断和修复，后续只能作为回归集，
不能继续作为未观察的独立泛化证据。

报告位置：

- `data/eval/reports/retrieval_holdout_1000_v1_expanded_after_weighting_v2.json`
- `data/eval/reports/retrieval_holdout_1000_v1_hybrid_after_weighting_v2.json`
- `data/eval/reports/retrieval_holdout_1000_v1_expanded_hybrid_after_weighting_v2.json`

## 7. 生成结果

在线图使用 `expanded-lexical-v1 + Dense + RRF`、`parent_context` 和 `assess`：

| 指标 | 结果 |
| --- | ---: |
| 通过 | 28/28 |
| 有答案准确率 | 1.000000 |
| 拒答准确率 | 1.000000 |
| 拒答 P/R/F1 | 1.000000 / 1.000000 / 1.000000 |
| 引用精确率 | 0.971429 |
| 引用召回率 | 1.000000 |
| 平均延迟 | 5687.155 ms |
| P95 延迟 | 9481.742 ms |

此前两条多证据失败样例 `gen-holdout-multi-farewell-home` 和
`gen-holdout-multi-hero-people` 均已恢复：前者能够分别返回阳关送别和洛城思乡证据，
后者能够返回个人气节和百姓疾苦证据。生成质量提升的同时，多证据类平均延迟达到
`8817.328 ms`，P95 为 `9940.227 ms`，是当前最明显的性能瓶颈。

报告位置：

- `data/eval/reports/generation_holdout_1000_v1_after_weighting_v2.json`

## 8. 接口与配置

公开 HTTP API、SSE 事件和数据库表结构不变。

SSE `retrieval` 事件现在通常返回：

```json
{
  "candidate_count": 30,
  "selected_count": 8,
  "strategy": "hybrid-rrf-v1"
}
```

基础设施故障降级时，`strategy` 可能为 `expanded-lexical-v1`。客户端必须把
`strategy` 当作诊断字段，不应依赖固定值。

新增配置：

| 环境变量 | 说明 |
| --- | --- |
| `CHAT_DENSE_MIN_SCORE` | 在线 Dense 分支的余弦相似度下限，默认 `0.60`，范围 `0.0` 到 `1.0` |

公开 `GET /api/v1/search/evidence` 仍固定为 `lexical-baseline-v1`，不接受 Dense、
Hybrid 或 Rerank 参数。

## 9. 测试与验证

| 层级 | 结果 |
| --- | --- |
| 后端全量测试 | `160 passed, 3 warnings` |
| Ruff | `All checks passed!` |
| 查询改写/Hybrid/检索定向测试 | `48 passed` |
| 真实检索评估 | 三条策略同集完成，在线组合 50/50 |
| 真实生成评估 | `28/28`、拒答 `7/7` |
| 真实服务 | MySQL、Qdrant、Qwen Embedding、DeepSeek 均完成调用 |

单元测试覆盖 Hybrid 在线组合、基础设施错误降级、非基础设施错误继续抛出、SSE 资源
关闭和生成评估资源关闭。

## 10. 风险与回滚

1. 50/28 条 holdout 仍属小样本，不能证明生产泛化。
2. 当前 holdout 已参与失败诊断和修复，只能作为回归集，不能继续声称独立泛化。
3. 在线 Hybrid 的平均延迟和 P95 均明显高于纯词法分支，多证据生成 P95 已接近 10 秒。
4. 白发展开规则仍是受控词典能力，下一步需要验证它是否泛化到更多“夸张、愁绪、
   时间流逝”问题，而不是只覆盖已知诗句。
5. Qdrant 或 Embedding 故障会降低召回，但不会导致问答接口不可用；这是可用性优先的
   降级，不代表降级结果与在线 Hybrid 等价。
6. 如果真实线上指标恶化，回滚方式是把在线检索组合退回 `ExpandedRetrievalService`
   并移除 Hybrid 分支，同时保留 Qdrant 索引和离线评估能力，不需要数据库迁移。
7. 当前没有旧向量清理、active index 切换和跨库对账，模型或切块升级前必须补齐。

## 11. 实施任务

- [x] 建立独立检索 holdout
- [x] 建立独立生成 holdout
- [x] 完成三条检索策略同集比较
- [x] 接入在线 Hybrid 与基础设施故障降级
- [x] 新增 `CHAT_DENSE_MIN_SCORE`
- [x] 补充 Chat/Hybrid 和资源生命周期测试
- [x] 更新 README、项目导览和前后端契约
- [x] 记录失败样例、风险和回滚方式
- [x] 完成加权 RRF、显式标题完整作品槽位和白发概念规则修复
- [x] 用当前代码重跑三条检索策略与生成 holdout

## 12. 后续方向

1. 新建下一批未观察的检索与生成 holdout，覆盖夸张愁绪、多证据和引用噪声。
2. 对在线 Hybrid 和多证据生成做并发、缓存、并行检索及候选裁剪优化。
3. 评估 Rerank、结构化过滤和低召回重写重试的收益与成本。
4. 验证 Qdrant HNSW 参数、检索延迟和 Embedding 维度升级。
5. 只有在下一批独立样本和性能预算稳定后，再考虑更高维度模型和云部署展示。

## 13. 决策与变更记录

| 日期 | 决策或变化 | 原因 |
| --- | --- | --- |
| 2026-09-23 | 使用独立 1000 首 holdout，不复用旧 50/28 条调参集 | 避免在观察集上继续调参和自证 |
| 2026-09-23 | 在线切换到 `expanded-lexical-v1 + Dense + RRF` | 当时同集 49/50，且无答案准确率保持 `1.0` |
| 2026-09-23 | Dense 使用 `CHAT_DENSE_MIN_SCORE=0.60` | 过滤明显跨域候选，同时保留语义召回 |
| 2026-09-23 | 仅对 Embedding/Qdrant 基础设施错误降级 | 保持可用性，不掩盖程序或数据错误 |
| 2026-09-23 | 引入加权 RRF 和显式标题完整作品槽位 | 修复标题问题被笔记挤占，以及秋浦歌/登高类失败 |
| 2026-09-23 | 新增 `white_hair_exaggeration` 受控概念规则 | 用可审查词典处理白发夸张，不增加样本 ID 特例 |
| 2026-09-23 | 当前 50/28 条样本转为回归集 | 样本已参与失败诊断，不能再作为独立泛化证据 |
| 2026-09-23 | 在线组合达到检索 50/50、生成 28/28 | 当前代码版本同集结果，性能成本另列为风险 |
