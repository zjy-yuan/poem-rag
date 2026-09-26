# 独立 1000 首 holdout v2

> 状态：已实现（回归集）
> 创建日期：2026-09-24
> 最近更新：2026-09-24
> 关联任务：建立第二批未观察评估集，复核查询扩展、加权 RRF 和 Dense 阈值的泛化能力

## 1. 背景与问题

`retrieval-holdout-1000-v1` 和 `generation-holdout-1000-v1` 已经参与失败诊断和
修复，只能作为回归集。继续在它们上面调参会把“记住样本”误当成“能力提升”，
因此需要一批问题不重叠、金标准重新核对的样本。

同时，v1 的失败集中在特定类别，v2 需要按类别重新分布，避免只覆盖已经修好的场景。

## 2. 目标

- 建立第二批检索和生成 holdout，问题文本与 v1 完全不重叠。
- 覆盖精确引用、短语、标题、作者、朝代、多证据、自然语言、长文本和结构化过滤。
- 覆盖跨域、领域内缺实体和领域内缺属性三类无答案样本。
- 用同一批样本比较 `expanded-lexical-v1`、`hybrid-rrf-v1` 和在线组合。
- 暴露通用检索问题，而不是增加样本 ID 特例。

## 3. 非目标

- 不修改公开 HTTP API、SSE 事件或数据库表结构。
- 不引入 Rerank、LLM 查询改写或多向量模型。
- 不把 v2 结果表述为生产泛化能力。

## 4. 数据契约

`data/eval/retrieval_holdout_1000_v2.json`，版本 `retrieval-holdout-1000-v2`：

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

`data/eval/generation_holdout_1000_v2.json`，版本 `generation-holdout-1000-v2`：

| 分类 | 条数 |
| --- | ---: |
| `poem_fact` | 6 |
| `natural_language` | 7 |
| `multi_evidence` | 3 |
| `refusal_missing_entity` | 3 |
| `refusal_missing_attribute` | 4 |
| `refusal_cross_domain` | 3 |
| 合计 | 26（16 有答案 + 10 拒答） |

契约测试保证：版本号、条数、分类覆盖、ID 与问题唯一，以及问题文本与 v1 不相交。
金标准和引用选择器必须在当前转换后语料中可定位。

## 5. 评估口径

- 检索：Top-5，金标准选择器匹配，指标为通过数、Recall@5、MRR、无答案准确率和延迟。
- 生成：真实 `deepseek-chat`，指标为通过数、有答案准确率、拒答准确率和引用
  P/R/F1。
- 检索路径和生成路径都复用在线实现，避免评估路径与线上路径漂移。

## 6. 检索结果

Top-5 同集结果：

| 策略 | 通过 | Recall@5 | MRR | 无答案准确率 | 平均延迟 | P95 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| `expanded-lexical-v1` | 38/46 | 0.815789 | 0.763158 | 0.875 | 411.059 ms | 1222.852 ms |
| `hybrid-rrf-v1` | 36/46 | 0.855263 | 0.761842 | 0.500 | 301.742 ms | 572.421 ms |
| `expanded-lexical-v1 + Dense + RRF`（修复前） | 40/46 | 0.868421 | 0.789474 | 0.875 | 952.536 ms | 2717.191 ms |
| `expanded-lexical-v1 + Dense + RRF`（修复后） | 45/46 | 1.000000 | 0.907895 | 0.875 | 874.575 ms | 2770.212 ms |

复现命令：

```powershell
.\.venv\Scripts\python.exe apps\api\scripts\evaluate_retrieval.py `
  --dataset data\eval\retrieval_holdout_1000_v2.json `
  --strategy expanded-hybrid --top-k 5 --min-score 0.60 `
  --json-output data\eval\reports\retrieval_holdout_1000_v2_expanded_hybrid_final.json
```

## 7. 生成结果

在线图使用 `expanded-lexical-v1 + Dense + RRF`、`parent_context` 和 `assess`：

| 阶段 | 通过 | 有答案准确率 | 拒答准确率 | 引用 P/R | 平均延迟 | P95 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 检索修复前 | 24/26 | 0.875000 | 1.000000 | 1.0 / 1.0 | 4633.567 ms | 7782.879 ms |
| 检索修复后 | 26/26 | 1.000000 | 1.000000 | 1.0 / 1.0 | 4699.931 ms | 8422.616 ms |

两条失败样例都缺必需事实：《梦游天姥吟留别》缺“且放白鹿青崖间”，《青玉案·元夕》
缺“东风夜放花千树”。修复后两条都能引用完整 `poem` 块并命中全部必需事实。

复现命令：

```powershell
.\.venv\Scripts\python.exe apps\api\scripts\evaluate_generation.py `
  --dataset data\eval\generation_holdout_1000_v2.json `
  --json-output data\eval\reports\generation_holdout_1000_v2_after_retrieval_fixes.json
```

## 8. 本轮修复

### 8.1 结构化主题查询保留正文候选

作者 + 朝代 + 体裁 + 主题类查询会先命中显式标题槽位。此前标题槽位占满输出后，
真正包含主题证据的正文候选被挤出。现在结构化主题查询在锚点之后保留一个
`poem`/`line` 主文本候选。

### 8.2 多标题查询优先完整作品块

显式标题命中可能只返回单行 chunk，导致整首作品的关键句子缺失。现在多标题查询
优先选择完整 `poem` 块，单行仍可作为补充证据。

### 8.3 粒度感知的 Dense 分数下限

全局 `CHAT_DENSE_MIN_SCORE=0.60` 不变，但 `poem` 和 `line` 主文本允许 `0.02`
容差（有效下限 `0.58`），`note` 仍严格使用调用方阈值。短诗和单行向量天然比长
注释向量分数低，统一阈值会把包含金标准证据的主文本整段过滤。

### 8.4 保持检索层失败样例

`no-answer-v2-xinqiji-office-08` 属于“话题命中、答案缺失”的领域内缺属性样本。
检索层召回相关诗句是正确行为，拒答由在线 `assess` 节点通过 LLM 结构化判定完成，
因此不增加样本特例。

## 9. 失败边界

1. 唯一检索失败是 `no-answer-v2-xinqiji-office-08`，说明检索层不能替代可答性判定。
2. 无答案准确率稳定在 `0.875`，与 v1 一致；剩余差异必须由 `assess` 处理。
3. 在线组合 P95 仍在 `2.7 s` 量级，`hybrid-rrf-v1` 虽快但无答案准确率只有 `0.5`。
4. v2 已经参与本轮失败诊断，后续只能作为回归集。

## 10. 风险与回滚

- 粒度容差只覆盖“主文本略低于阈值”这一种情况，更长的作品和更短的词牌需要在 v3 复核。
- 评估延迟受本机、Qdrant 缓存和模型服务状态影响，只能做同环境趋势比较。
- 回滚方式：把 Dense 过滤还原为所有粒度统一阈值，或把在线检索退回
  `ExpandedRetrievalService`；两者都不涉及数据库迁移和公开接口变更。

## 11. 实施任务

- [x] 建立 v2 检索 holdout（46 条）
- [x] 建立 v2 生成 holdout（26 条）
- [x] 增加覆盖度、唯一性和金标准可定位性契约测试
- [x] 新增只读检索剖析脚本
- [x] 修复结构化主题和多标题候选选择
- [x] 引入粒度感知 Dense 阈值并补充回归测试
- [x] 重跑三条检索策略并记录真实指标
- [x] 用 v2 生成 holdout 复核检索修复的端到端影响
- [ ] 建立 v3 样本前不把 v2 结果写成生产结论

## 12. 决策与变更记录

| 日期 | 决策或变化 | 原因 |
| --- | --- | --- |
| 2026-09-24 | 新建 v2 检索和生成 holdout，问题与 v1 不重叠 | v1 已参与诊断，不能继续作为独立泛化证据 |
| 2026-09-24 | 保持 `CHAT_DENSE_MIN_SCORE=0.60` 全局不变 | 拒答能力和召回质量不能靠整体降阈值交换 |
| 2026-09-24 | 只对 `poem`/`line` 放宽 `0.02`，`note` 不放宽 | 短文本向量分数天然低于长注释，阈值应按粒度区分 |
| 2026-09-24 | 结构化主题查询保留正文候选 | 标题槽位不能挤掉主题证据 |
| 2026-09-24 | 多标题查询优先完整作品块 | 单行命中会导致关键句子缺失 |
| 2026-09-24 | 不特判 `no-answer-v2-xinqiji-office-08` | 拒答是 `assess` 的职责，检索层特判会掩盖真实能力 |
| 2026-09-24 | v2 转为回归集 | 样本已参与失败诊断 |
| 2026-09-24 | 检索修复后生成复跑为 26/26 | 完整作品槽位同时改善了端到端必需事实覆盖 |
