# 独立 1000 首 holdout v3

> 状态：已实现（回归集）
> 创建日期：2026-09-24
> 最近更新：2026-09-24
> 关联任务：建立第三批未观察评估集，复核批量 Embedding、粒度容差和查询变体上限的泛化能力

## 1. 背景与问题

`retrieval-holdout-1000-v1`、`generation-holdout-1000-v1` 和对应的 v2 数据集都已
参与失败诊断或实现调参，只能作为回归集。为了继续检查当前在线策略是否会对新问题
稳定生效，需要建立第三批问题与金标准均未参与此前进化过程的数据。

v3 的重点不是继续修补样本，而是回答三个问题：

1. 批量 Embedding 和进程级资源复用是否保持检索质量。
2. 粒度感知 Dense 阈值是否适用于新的长诗、短词和小令。
3. 查询变体上限是否仍能覆盖多证据、总结和结构化筛选问题。

## 2. 目标

- 建立第三批检索和生成 holdout，问题文本与 v1/v2 完全不重叠。
- 覆盖精确引用、短语、标题、作者、朝代、多证据、自然语言、长文本和结构化筛选。
- 覆盖跨域、领域内缺实体和领域内缺属性三类无答案样本。
- 使用当前在线策略执行一次真实评估，记录质量、延迟和失败边界。
- 创建后立即冻结 v3；若未来根据 v3 结果修改实现，必须另建 v4 复核泛化。

## 3. 非目标

- 不修改公开 HTTP API、SSE 事件或数据库表结构。
- 不引入 Rerank、缓存或新的检索策略。
- 不根据 v3 的单条失败修改代码、提示词、权重或阈值。
- 不把 46/26 条小样本结果表述为生产级泛化能力。

## 4. 数据契约

`data/eval/retrieval_holdout_1000_v3.json`，版本 `retrieval-holdout-1000-v3`：

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

`data/eval/generation_holdout_1000_v3.json`，版本 `generation-holdout-1000-v3`：

| 分类 | 条数 |
| --- | ---: |
| `poem_fact` | 6 |
| `natural_language` | 7 |
| `multi_evidence` | 3 |
| `refusal_missing_entity` | 3 |
| `refusal_missing_attribute` | 4 |
| `refusal_cross_domain` | 3 |
| 合计 | 26（16 有答案 + 10 拒答） |

契约测试保证版本号、条数、分类覆盖、ID 与问题唯一、问题文本与 v1/v2 不相交，以及
金标准可在当前 1000 首转换语料中定位。创建后额外做集合校验，v3 检索金标准作品和
生成引用作品均与 v1/v2 无交集。

## 5. 评估口径

- 检索：Top-5，使用金标准选择器匹配，记录通过数、Recall@5、MRR、无答案准确率和延迟。
- 生成：真实 `deepseek-chat`，记录通过数、有答案准确率、拒答准确率和引用 P/R/F1。
- 检索和生成都复用在线实现，避免评估路径与线上策略漂移。
- 本轮只观测 v3 一次；报告生成后不用于选择实现参数。

## 6. 检索结果

Top-5 同集结果：

| 策略 | 通过 | Recall@5 | MRR | 无答案准确率 | 平均延迟 | P95 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| `expanded-hybrid-rrf-v1` | 45/46 | 1.000000 | 0.929825 | 0.875 | 519.561 ms | 1280.804 ms |
| `expanded-lexical-v1` | 44/46 | 0.973684 | 0.890351 | 0.875 | 220.978 ms | 776.836 ms |
| `hybrid-rrf-v1` | 35/46 | 0.815789 | 0.759649 | 0.625 | 266.291 ms | 431.833 ms |

在线组合同样是检索质量最高的路径。唯一失败为
`no-answer-v3-dufu-death-year-08`：问题询问“杜甫去世于哪一年”，检索层召回了
杜甫相关注释，但没有直接年份证据。该失败属于领域内缺属性问题，在线 `assess`
已正确拒答，因此本轮不据此增加检索层特例。

纯 `expanded-lexical-v1` 的额外失败是 `structured-v3-libai-xinglu-01`。纯
`hybrid-rrf-v1` 的失败更集中，且无答案准确率只有 `0.625`，说明查询扩展与在线
融合仍然是必要组合。

报告：

- `data/eval/reports/retrieval_holdout_1000_v3_expanded_hybrid.json`
- `data/eval/reports/retrieval_holdout_1000_v3_expanded.json`
- `data/eval/reports/retrieval_holdout_1000_v3_hybrid.json`

## 7. 生成结果

在线图使用 `expanded-lexical-v1 + Dense + RRF`、`parent_context` 和 LLM `assess`：

| 指标 | 结果 |
| --- | ---: |
| 通过 | 25/26 |
| 有答案准确率 | 0.937500 |
| 拒答准确率 | 1.000000 |
| 拒答 P/R/F1 | 1.0 / 1.0 / 1.0 |
| 引用精确率/召回率 | 1.0 / 1.0 |
| 平均延迟 | 4100.890 ms |
| P95 | 6456.598 ms |

唯一失败为 `gen-v3-guazhou-homesick`。问题询问王安石《泊船瓜洲》如何借江南春色
写归乡之情，回答命中了“春风又绿江南岸”，但缺少
`distance` 事实“京口瓜洲一水间 / 钟山只隔数重山”。引用 P/R 仍为 `1.0`，
回答语义基本正确，主要问题是严格事实覆盖不足。该样本不触发实现修改。

报告：`data/eval/reports/generation_holdout_1000_v3.json`。

## 8. 复现命令

```powershell
.\.venv\Scripts\python.exe apps\api\scripts\evaluate_retrieval.py `
  --dataset data\eval\retrieval_holdout_1000_v3.json `
  --strategy expanded-hybrid --top-k 5 --min-score 0.60 `
  --json-output data\eval\reports\retrieval_holdout_1000_v3_expanded_hybrid.json

.\.venv\Scripts\python.exe apps\api\scripts\evaluate_generation.py `
  --dataset data\eval\generation_holdout_1000_v3.json `
  --json-output data\eval\reports\generation_holdout_1000_v3.json
```

生成评估需要真实的 MySQL、Qdrant、Qwen Embedding 和 DeepSeek 配置。没有完整
Provider 环境时，只能复现契约测试，不能复现端到端生成报告。

## 9. 失败边界

1. 检索层仍不能独立判定“话题命中但答案缺失”，拒答主要由在线 `assess` 完成。
2. 无答案准确率仍为 `0.875`，与 v1/v2 一致，说明剩余难点不是单次检索排序。
3. 生成评估仍使用严格事实短语匹配，可能把语义正确但措辞不同的回答判为失败。
4. v3 是 46/26 条小样本，且已经完成真实观察，只能作为回归集。
5. 本轮未验证跨请求并发、缓存或更大语料规模下的性能上限。

## 10. 风险与回滚

- v3 生成报告只有一次运行，模型输出和网络状态都会带来波动，不能把单次延迟写成
  稳定性能结论。
- Dense 阈值容差和新候选选择规则可能在更长作品、更多实体或跨朝代查询上失效，
  后续泛化验证必须新建 v4。
- 回滚 v3 不涉及生产实现；删除或忽略数据集即可停止回归，但不应删除已经记录的报告。
- 如果未来根据 v3 修改实现，应把 v3 标记为已观察回归集，并另建 v4 作为独立复核。

## 11. 测试计划

| 层级 | 覆盖内容 |
| --- | --- |
| 检索契约测试 | v3 版本、46 条、分类覆盖、ID/问题唯一、与 v1/v2 问题不相交、金标准可定位 |
| 生成契约测试 | v3 版本、26 条、答案/拒答分类、ID/问题唯一、与 v1/v2 问题不相交、引用可定位 |
| 真实检索评估 | 三条策略在 v3 上运行并落盘报告 |
| 真实生成评估 | 在线 `RagChatGraph` 在 v3 上运行并落盘报告 |
| 回归验证 | 后端全量 pytest、Ruff，以及现有 v1/v2 契约测试 |

定向测试结果：检索评估契约测试 `12 passed`，生成评估契约测试 `12 passed`。

## 12. 实施任务

- [x] 建立 v3 检索 holdout（46 条）
- [x] 建立 v3 生成 holdout（26 条）
- [x] 增加版本、数量、分类、唯一性和问题不相交契约测试
- [x] 校验金标准和引用选择器可在 1000 首语料中定位
- [x] 运行三条检索策略并记录真实指标
- [x] 运行在线生成图并记录端到端指标
- [x] 记录失败样本、风险和复现命令
- [x] 冻结 v3 为回归集
- [ ] 建立 v4 后才允许基于 v3 失败修改实现

## 13. 决策与变更记录

| 日期 | 决策或变化 | 原因 |
| --- | --- | --- |
| 2026-09-24 | 新建 v3 检索和生成 holdout，问题与 v1/v2 不重叠 | v1/v2 已参与诊断，不能继续作为独立泛化证据 |
| 2026-09-24 | 纳入长诗尾部、多标题、结构化筛选和三类拒答 | 复核此前修复和新阈值是否覆盖主要边界 |
| 2026-09-24 | 保留 `no-answer-v3-dufu-death-year-08` 的检索失败 | 检索层召回相关材料是正确行为，缺属性判断属于 `assess` |
| 2026-09-24 | 不为 `gen-v3-guazhou-homesick` 修改提示词或事实规则 | 单条表现不足以证明通用收益，避免样本级过拟合 |
| 2026-09-24 | v3 创建后冻结为回归集 | 数据集已经过真实观察，不能继续冒充未观察泛化证据 |
