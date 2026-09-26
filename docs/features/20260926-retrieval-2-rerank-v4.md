# Retrieval 2.0：候选扩展、离线重排与 v4 泛化集

> 状态：已实现，策略未达切换门槛，在线未切换
> 创建日期：2026-09-26
> 最近更新：2026-09-26
> 关联任务：在不修改在线 RAG 的前提下，建立可替换的 Rerank 接口，并用未观察 v4 验证策略收益。

## 1. 背景与问题

v3 在线组合在 `retrieval-holdout-1000-v3` 上达到 `45/46`、Recall@5 `1.0`、
MRR `0.929825`。该结果说明候选召回已接近当前样本上限，但当前指标不能回答两个问题：

1. 召回扩展后，Rerank 是否能把正确的正文证据进一步前移。
2. 现有 Top-5 策略换一批独立问题后是否仍稳定；v1、v2、v3 都已参与观察，不能再作为
   新策略的泛化证据。

因此本轮先冻结 `retrieval-holdout-1000-v4`，再实现模型无关的离线重排框架。只有 v4
质量和延迟均达到切换门槛，才允许修改在线问答检索。实测结论是策略未达门槛：
`deterministic-evidence-v1` 在 v4 上 Recall@5 从 `1.0` 降到 `0.828947`，MRR 和
nDCG@5 同时低于基线，因此保留离线接口与报告，在线继续使用 `expanded-hybrid-rrf-v1`。

## 2. 目标

- 建立 v4 检索 holdout：46 条，问题与 v1/v2/v3 全部不相交，金标准作品不复用旧集标题。
- 为检索评估增加 `nDCG@k`，区分“召回正确”和“排序靠前”。
- 新增 `EvidenceReranker` 协议和 `RerankedRetrievalService` 组合层。
- 首版实现确定性的 `DeterministicEvidenceReranker`，不新增模型、网络或商业 API 依赖。
- 采用“召回 Top-30 -> 重排 -> 输出 Top-5”，策略名独立版本化。
- 在 v3 回归集和 v4 泛化集上分别运行，记录 Recall@5、MRR、nDCG@5、P95 和失败样本。
- 默认保持在线 `expanded-hybrid-rrf-v1` 不变。

## 3. 非目标

- 不修改公开 HTTP、SSE、前端、数据库表或 Alembic 迁移。
- 不同时更换 Embedding 模型、向量维度、生成模型或 Prompt。
- 不下载或部署 Cross-Encoder/BGE Reranker；本轮只提供可替换接口和确定性对照。
- 不依据 v4 的单条失败调整在线阈值、词典或权重。
- 不把 46 条样本的提升表述为生产级泛化能力。

## 4. 用户场景与验收标准

| 场景 | 操作 | 预期结果 |
| --- | --- | --- |
| 旧策略回归 | 在 v3 上运行 `expanded-hybrid` | 现有指标可复现，nDCG 字段有值 |
| 候选重排 | 在 v3/v4 上运行 `expanded-hybrid-rerank` | 返回 Top-5，报告记录候选数和重排策略 |
| 泛化检查 | 在 v4 上比较两种策略 | 问题不相交；不达标则不切换在线 |
| 故障回退 | 在线策略保持不变 | 删除组合层即可回退，无数据迁移 |

实测结果：三个场景全部按预期执行，泛化检查结论为“不达标，不切换”。旧策略在 v3
复现为 45/46，Rerank 在 v4 为 38/46，低于基线 45/46。

## 5. 方案概览

```text
query
  -> Expanded + Hybrid RRF
  -> Top-30 candidates
  -> EvidenceReranker
  -> Top-5 evidence
```

`RerankedRetrievalService` 只负责候选扩展和结果协议转换，不包含具体排序算法。
`EvidenceReranker` 定义输入 query、候选证据和输出上限；后续可以替换为 BGE、Qwen
或远程 Rerank Provider，而无需修改评估脚本和在线组合层。

首版确定性重排使用现有证据的规范化正文、标题、作者和朝代信息，并与上游融合分组合。
它只证明离线框架、版本化和指标闭环可行，不宣称具备 Cross-Encoder 的语义理解能力。

## 6. 接口与契约

- 不新增或修改公开 HTTP API。
- 内部新增 `EvidenceReranker.rerank(query, evidence, limit)` 异步协议。
- 新增组合策略名 `expanded-hybrid-rerank-v1`。
- `candidate_limit` 默认 30，且必须不小于最终 Top-k。
- 上游 `hard_filtered`、`normalized_query` 和故障错误语义保持透传。
- 任一 Reranker 异常默认向上抛出，不在本轮静默降级到未重排结果。
- 评估脚本新增 `--strategy expanded-hybrid-rerank` 和 `--rerank-candidate-limit`。

## 7. 数据设计

新增 `data/eval/retrieval_holdout_1000_v4.json`，版本 `retrieval-holdout-1000-v4`：

| 分类 | 条数 |
| --- | ---: |
| `exact_quote` | 6 |
| `phrase` | 7 |
| `title` | 4 |
| `author` | 3 |
| `dynasty` | 3 |
| `multi_evidence` | 3 |
| `natural_language` | 5 |
| `long_form` | 5 |
| `structured_filter` | 2 |
| `no_answer_cross_domain` | 3 |
| `no_answer_in_domain_missing_entity` | 2 |
| `no_answer_in_domain_missing_attribute` | 3 |
| 合计 | 46（38 有答案 + 8 无答案） |

不新增数据库表。v4 创建后冻结；若依据 v4 做样本级修复，后续泛化必须另建 v5。

## 8. 后端设计

- `app/services/reranking.py`：协议、确定性和组合服务。
- `app/evaluation/retrieval.py`：新增 nDCG@5 和汇总口径。
- `apps/api/scripts/evaluate_retrieval.py`：接入离线重排策略。
- `tests/test_reranking.py`：覆盖候选数、Top-k、排序稳定性、协议透传和异常。
- `tests/test_retrieval_evaluation.py`：覆盖 v4 契约、旧集问题不相交、金标准可定位。

## 9. 前端设计

不涉及。

## 10. RAG 与评估

- 语料：`chinese-gushiwen-1000-v2` 的当前 1000 首转换记录。
- 上游：`expanded-hybrid-rrf-v1`。
- 候选数：30；输出：Top-5。
- Reranker：`deterministic-evidence-v1`。
- 指标：通过数、Recall@5、MRR、nDCG@5、无答案准确率、平均延迟和 P95。
- 成本：本轮不新增模型 Token 成本；只比较 CPU 重排延迟。
- 在线切换门槛：v4 的 nDCG@5 或 MRR 有明确提升，且 Recall@5 不下降，P95 增量可接受。

### 10.1 真实结果

v3 回归集，Top-5、`min-score=0.60`：

| 策略 | 通过 | Recall@5 | nDCG@5 | MRR | 平均延迟 | P95 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| `expanded-hybrid-rrf-v1` | 45/46 | 1.000000 | 0.947993 | 0.929825 | 545.027 ms | 1322.895 ms |
| `expanded-hybrid-rerank-v1` | 43/46 | 0.947368 | 0.893808 | 0.877193 | 489.194 ms | 1367.635 ms |

v4 泛化集，Top-5、`min-score=0.60`：

| 策略 | 通过 | Recall@5 | nDCG@5 | MRR | 平均延迟 | P95 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| `expanded-hybrid-rrf-v1` | 45/46 | 1.000000 | 0.915410 | 0.885965 | 543.553 ms | 1361.468 ms |
| `expanded-hybrid-rerank-v1`，Top-30 候选 | 38/46 | 0.828947 | 0.776326 | 0.757456 | 550.844 ms | 1285.898 ms |
| `expanded-hybrid-rerank-v1`，Top-10 候选 | 41/46 | 0.907895 | 0.821863 | 0.792544 | 550.525 ms | 1257.585 ms |
| `expanded-hybrid-rerank-v1`，Top-5 候选 | 45/46 | 1.000000 | 0.860899 | 0.807895 | 552.056 ms | 1254.318 ms |

候选预算消融说明：扩大上游候选本应给重排更多选择，但本实现反而更容易把长注释或
赏析 chunk 排到正文之前。Top-30 和 Top-10 都存在 Recall@5 损失；Top-5 不丢召回，
但 MRR 与 nDCG 仍低于不重排基线，说明问题在排序信号本身，而不是单纯候选供给不足。

### 10.2 失败模式

- `natural-v4-choule-tian-yangzhou-04`、`long-v4-guanju-pursuit-01` 等正文问题被
  长注释或赏析 chunk 挤占，字符 bigram 覆盖与长文本长度正相关。
- `multi-v4-yuanri-qingming-01`、`multi-v4-qiuci-tianjingsha-03` 暴露同作品多证据
  覆盖不足，per-poem 限制只约束占位数量，不能保证互补证据进入 Top-5。
- `structured-v4-luyou-plum-02` 显示确定性元数据匹配权重对结构化主题查询偏弱。
- 8 条无答案问题中 `no-answer-v4-lushan-location-07` 在四种预算下均失败，重排未改善
  “话题命中、答案缺失”的领域内判定，该类问题仍需在线 `assess` 兜底。

本轮不依据上述单条样本调整权重、词典或阈值；任何进一步调参都需要新的未观察 v5。

### 10.3 结论与边界

- 离线协议、版本化策略、nDCG 指标和报告链路有效，可以继续承载后续 Reranker 研究。
- `deterministic-evidence-v1` 明确未达到切换门槛，不能表述为效果提升。
- 在线问答、SSE、公开 API、数据库和前端均未改变。
- 46 条样本只能支持本阶段筛选结论，不能声明生产级泛化能力。

## 11. 测试计划

| 层级 | 覆盖内容 |
| --- | --- |
| 单元测试 | Reranker 排序、稳定 tie-break、Top-k 截断、候选上限 |
| 集成测试 | 组合服务透传查询、过滤、硬过滤和错误 |
| 契约测试 | v4 版本、数量、分类、唯一性、旧集不相交、金标准可定位 |
| 评估 | v3 回归和 v4 泛化各运行 `expanded-hybrid` 与 `expanded-hybrid-rerank` |
| 回归 | 后端 pytest、Ruff、前端 typecheck、Vitest、生产构建 |

已执行的定向验证：Ruff `All checks passed!`；检索评估与重排测试
`22 passed, 2 warnings`。完整 `.\scripts\verify.ps1` 作为本轮收口门禁单独执行。

## 12. 风险与回滚

- 确定性重排可能因重复正文造成同作品占位，需通过 per-poem 限制和 v4 检查。
- nDCG 的金标准选择器可能匹配多个 chunk；实现必须避免同一 gold 被重复计分。
- v4 仍是 46 条小样本，结论只能用于本阶段策略筛选。
- 若 v4 未显示收益，保留接口和报告，不切换在线；删除组合层即可回滚。
- 后续引入模型 Reranker 时需单独评估延迟、显存、下载源、许可和失败降级。

实测已触发第一条回滚条件。当前保留 `apps/api/app/services/reranking.py` 作为离线
实验能力，不作为在线检索路径；在线默认仍是 `expanded-hybrid-rrf-v1`。

## 13. 实施任务

- [x] 冻结功能边界和非目标
- [x] 新增 ADR-001
- [x] 建立 v4 数据集和契约测试
- [x] 增加 nDCG@k
- [x] 实现 Rerank 协议、确定性和组合服务
- [x] 接入离线评估脚本
- [x] 运行 v3 回归和 v4 泛化
- [x] 更新项目指南、功能索引和开发日志

后续候选任务（不属于本轮范围）：

- 评估模型型 Cross-Encoder/BGE Reranker，先补延迟、依赖、显存、许可和失败降级 ADR。
- 在引入模型前先优化候选去重、正文优先和同作品互补证据覆盖。
- 若再次验证，冻结新的 v5 泛化集，v4 转为回归集。

## 14. 决策与变更记录

| 日期 | 决策或变化 | 原因 |
| --- | --- | --- |
| 2026-09-26 | 先冻结 v4，再实现 Rerank | 避免根据待验证数据反向调参 |
| 2026-09-26 | 首版只做确定性 Reranker | 先验证框架、指标和候选扩展，不引入不可控模型成本 |
| 2026-09-26 | 默认不切换在线策略 | v3 已观察，必须先用 v4 取得独立证据 |
| 2026-09-26 | 拒绝启用 `deterministic-evidence-v1` | v4 上 Recall@5、MRR、nDCG@5 均低于基线，且候选预算消融无法修复排序信号 |
| 2026-09-26 | 保留离线 Rerank 框架为实验能力 | 接口、版本化和指标闭环已验证有效，后续可替换模型型 Reranker |
