# 生成层评估骨架

> 状态：已实现（确定性指标 + 真实基线）  
> 创建日期：2026-09-20  
> 最近更新：2026-09-20  
> 关联任务：生成质量、引用准确率、拒答 P/R/F1 和真实模型回归

## 1. 背景与问题

项目已经具备检索层评估、真实 MySQL + SSE 基础联调和 `assess` 可答性判定，但还没有
固定方式回答以下问题：

1. 有证据问题是否给出了包含关键事实的回答。
2. 回答是否引用了正确的诗词证据。
3. 无答案问题是否稳定拒答，是否误拒有答案问题。
4. 更换 Prompt、模型或检索策略后，生成质量是否发生回归。

只验证“能流式返回”不等于验证生成质量。本切片先建立可复现、确定性的评估骨架，
把答案事实、引用和拒答行为变成可比较的指标。

## 2. 目标

- 增加版本化生成评估数据集，样本不依赖自增 ID。
- 通过 `RagChatGraph.stream()` 直接运行在线图，复用真实 Chat Provider。
- 每个样本使用独立数据库 Session，避免前一样本的事务状态污染后一样本。
- 逐样本隔离异常，单个模型或数据库失败不能中断整份报告。
- 输出答案正确率、引用精确率/召回率、拒答 P/R/F1、平均延迟和 P95。
- 保存完整 JSON 报告，记录失败样本和关键中间结果。

## 3. 非目标

- 首版不实现 LLM-as-judge，不把模型自评当作事实正确率。
- 首版不计算 Token 成本、忠实度蕴含或人工文学赏析评分。
- 不在普通单元测试中调用真实 DeepSeek，真实评估由 CLI 手动执行。
- 不修改公开 HTTP API、SSE 事件或数据库表结构。
- 不因为评估骨架通过就宣称生成质量已经达到上线标准。

## 4. 用户场景与验收标准

| 场景 | 操作 | 预期结果 |
| --- | --- | --- |
| 有证据问答 | 运行固定问题 | 回答包含 `required_facts`，不包含 `forbidden_facts`，并引用金标准作品 |
| 无答案拒答 | 运行领域内或跨域问题 | 返回固定拒答，不产生引用 |
| 引用缺失 | 模型回答正确但没有引用 | 该样本失败，引用召回率下降 |
| 错误引用 | 模型引用非金标准作品 | 引用精确率下降，报告保留引用快照 |
| 单样本失败 | Provider 超时或非法图事件 | 该样本记录受控错误码，后续样本继续执行 |
| 报告复现 | 指定数据集和 JSON 输出路径 | 报告包含模型、策略、样本结果、汇总指标和时间戳 |

## 5. 方案概览

```text
data/eval/generation_rag_v1.json
  -> GenerationEvaluator
  -> 每个 case 打开独立 MySQL Session
  -> RagChatGraph.stream()
  -> 捕获 retrieval / delta / citation / final 事件
  -> required_facts、forbidden_facts、expected_citations 判定
  -> GenerationEvaluationReport
  -> 控制台摘要 + 可选 JSON 报告
```

关键取舍：

1. 直接运行在线图，而不是复制 Prompt 和检索逻辑；评估路径必须和用户路径一致。
2. 事实使用 `any_of` 候选词，避免只认一个同义词导致假失败。
3. 引用金标准使用 `EvidenceSelector`，但生成层不支持行号选择器，因为当前
   `CitationDraft` 只保存作品、作者、朝代、粒度和文本快照。
4. `passed` 是严格判定，包含事实、拒答决策和引用召回；`answer_accuracy` 单独表示
   回答事实是否正确，便于定位是回答错误还是引用错误。
5. 首版指标是确定性的，后续才增加 LLM-as-judge 或人工忠实度评分。

## 6. 接口与契约

本切片不新增 HTTP 接口，新增离线 CLI：

```powershell
.\.venv\Scripts\python.exe apps\api\scripts\evaluate_generation.py `
  --json-output data\eval\reports\generation_rag_v1_20260920.json
```

前置条件：

1. MySQL 已升级到 Alembic head，并且当前种子诗词和 chunks 已存在。
2. 在线检索依赖的 `expanded-lexical-v1` 可运行。
3. `DEEPSEEK_API_KEY` 已配置，模型可通过真实网络访问。

CLI 只输出受控指标和模型 ID，不输出 API Key、完整上游响应或向量内容。

## 7. 数据设计

数据集：`data/eval/generation_rag_v1.json`

每个样本包含：

1. `id`、`category`、`question`。
2. `expected=answer|refusal`。
3. `required_facts`：每个事实有稳定 ID 和 `any_of` 候选词。
4. `forbidden_facts`：回答中不得出现的事实。
5. `expected_citations`：可移植的引用选择器。

首版共 12 条：8 条有答案，4 条拒答，覆盖月亮、思乡、写景、哲理、意象、主题、
领域内缺实体、领域内缺属性和跨域问题。评估集只基于当前 6 首种子诗词，不能代表
开放语料分布。

## 8. 后端设计

主要模块：

```text
app/schemas/generation_evaluation.py
app/evaluation/generation.py
apps/api/scripts/evaluate_generation.py
apps/api/tests/test_generation_evaluation.py
```

职责边界：

1. Schema 负责数据集、样本结果和报告结构校验。
2. Evaluator 负责事件收集、逐样本隔离和指标计算。
3. Graph Factory 负责每个样本的 Session 生命周期和在线图构造。
4. CLI 负责真实 Provider、Engine 和报告文件生命周期。

指标定义：

| 指标 | 定义 |
| --- | --- |
| `answer_accuracy` | 有答案样本中，未拒答、必需事实命中且禁用事实未出现的比例 |
| `citation_precision` | 实际引用中匹配任一金标准选择器的比例 |
| `citation_recall` | 金标准引用选择器至少被一条实际引用匹配的比例 |
| `refusal_precision` | 所有拒答中，本应拒答的比例 |
| `refusal_recall` | 所有应拒答样本中，实际拒答的比例 |
| `refusal_f1` | 拒答精确率和召回率的调和平均 |
| `pass_rate` | 事实、拒答决策和引用完整性全部通过的样本比例 |

## 9. 前端设计

不涉及前端页面和交互。

## 10. RAG 与评估

评估固定使用在线 `expanded-lexical-v1`、当前 `ChatModelPort` 和
`rewrite -> retrieve -> assess -> generate|refuse -> validate` 图。报告保存：

1. 检索策略、候选数和入选数。
2. 回答文本和 `finish_reason`。
3. 必需事实命中/缺失、禁用事实命中。
4. 实际引用快照、金标准引用命中/缺失。
5. 延迟、错误码和失败样本 ID。

首版不把回答文本写入日志；完整回答只进入本地 JSON 报告，便于人工复核。

## 11. 测试计划

| 层级 | 覆盖内容 |
| --- | --- |
| 单元测试 | 指标算术、引用匹配、事实匹配、Schema 约束 |
| 集成测试 | 逐样本 Session/Graph Factory、单样本异常隔离 |
| 契约测试 | CLI `--help`、数据集版本和类别覆盖 |
| 真实评估 | 手动运行真实 DeepSeek，保存 JSON 报告并复核失败样本 |
| 回归评估 | Prompt、模型、检索策略变更前后使用同一数据集比较 |

## 12. 风险与回滚

1. 12 条样本不足以代表开放语料，指标只能作为回归基线。
2. `required_facts` 仍是关键词匹配，不能证明事实被证据蕴含。
3. 真实模型有随机性，温度固定为 `0` 仍可能存在供应商侧差异。
4. 评估会消耗真实 Token，需要控制数据集规模和运行频率。
5. 如评估逻辑出现缺陷，可删除报告并停止 CLI，不影响在线问答链路。

## 13. 实施任务

- [x] 文档与数据契约
- [x] Schema 与数据集
- [x] Evaluator 与 CLI
- [x] 单元测试和错误隔离
- [x] 真实 DeepSeek 基线与报告归档
- [ ] LLM-as-judge 或人工忠实度评分

## 14. 真实基线与结论

真实 `deepseek-chat` 基线共运行四次，报告保存在 `data/eval/reports/`：

| 报告 | 通过 | 关键变化 |
| --- | ---: | --- |
| `generation_rag_v1_20260920.json` | 5/12 | 真实模型首跑，7 条失败 |
| `generation_rag_v1_after_literal_20260920.json` | 10/12 | 查询改写加入《标题》和引号字面量抽取 |
| `generation_rag_v1_after_corpus_repair_20260920.json` | 11/12 | 修复种子语料漂移与事实口径，只剩《水调歌头》 |
| `generation_rag_v1_after_parent_context_20260920.json` | 12/12 | 在线检索追加诗词级父级上下文 |

最终一次报告的关键指标：`answer_accuracy=1.0`、`citation_precision=1.0`、
`citation_recall=1.0`、`refusal_f1=1.0`、平均延迟 `1510.694 ms`、
P95 `3230.334 ms`。

结论与边界：

1. 失败样本的根因依次是“查询字面量未参与检索”“语料正文缺失”“行级命中没有带上
   整篇作品”，都不是 Prompt 问题；修 Prompt 只会掩盖结构缺陷。
2. `after_literal` 报告早于数据集口径修正和语料修复，其中的
   `gen-denggueque-reason` 失败属于过期结论，不作为缺陷证据。
3. `12/12` 只说明当前 12 条固定样本通过，不能代表开放语料上的生成质量；语料扩充后
   必须重新标注并重跑。
4. 关键词事实匹配只能验证“回答提到了关键信息”，不能证明事实被证据蕴含，忠实度
   仍需 entailment 或人工评分。

## 15. 决策与变更记录

| 日期 | 决策或变化 | 原因 |
| --- | --- | --- |
| 2026-09-20 | 首版只做确定性事实、引用和拒答指标 | 先建立可复现基线，避免模型自评掩盖问题 |
| 2026-09-20 | 评估直接运行 `RagChatGraph.stream()` | 保证评估路径与在线问答路径一致 |
| 2026-09-20 | 每个样本使用独立数据库 Session | 避免前一样本事务和身份状态污染后续样本 |
| 2026-09-20 | 单样本异常记录错误码后继续 | 一份评估报告应能暴露所有失败样本，而不是提前中断 |
| 2026-09-20 | 失败样本按“检索、语料、评估口径、组装”分类定位 | 避免用调 Prompt 掩盖结构缺陷 |
| 2026-09-20 | 历史报告保留原样，不因口径变更回写 | 报告是历史证据，覆盖会破坏可追溯性 |
| 2026-09-20 | 评估与在线问答共用 `build_chat_retrieval` | 证据组装链一旦分叉，评估结论就失去意义 |
