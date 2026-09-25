# 生成层评估骨架

> 状态：已实现（确定性指标 + 真实基线；已有独立 LLM-as-judge 增量）
> 创建日期：2026-09-20  
> 最近更新：2026-09-24
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

- 首版不实现 LLM-as-judge；该项已在 2026-09-24 作为读取现有报告的独立离线增量实现，
  见 [生成质量 LLM-as-judge](20260924-generation-judge.md)。
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
data/eval/generation_rag_open_corpus_v1.json
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
  --json-output data\eval\reports\generation_rag_open_corpus_v1_20260920.json
```

前置条件：

1. MySQL 已升级到 Alembic head，并且当前种子诗词和 chunks 已存在。
2. 在线检索依赖的 `expanded-lexical-v1` 可运行。
3. `DEEPSEEK_API_KEY` 已配置，模型可通过真实网络访问。

CLI 只输出受控指标和模型 ID，不输出 API Key、完整上游响应或向量内容。

## 7. 数据设计

数据集：`data/eval/generation_rag_open_corpus_v1.json`

每个样本包含：

1. `id`、`category`、`question`。
2. `expected=answer|refusal`。
3. `required_facts`：每个事实有稳定 ID 和 `any_of` 候选词。
4. `forbidden_facts`：回答中不得出现的事实。
5. `expected_citations`：可移植的引用选择器。

当前默认集共 28 条：21 条有答案、7 条拒答，覆盖真实作品事实、自然语言、长诗长词、
典故、多证据和三类拒答边界。旧 `data/eval/generation_rag_v1.json` 共 12 条，基于
6 首种子诗词，已冻结为历史回归集，不再作为默认口径。

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

1. 旧 12 条样本不足以代表开放语料，当前默认 28 条 holdout 也仍不足以完成统计显著
   评估。
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
- [x] LLM-as-judge 或人工忠实度评分

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

### 14.1 当前开放语料 holdout

默认评估集已切换为 `generation-rag-open-corpus-100-v1`，真实 `deepseek-chat` 结果为
`28/28`：有答案准确率、拒答准确率、拒答 F1、引用精确率和引用召回率均为 `1.0`，
平均延迟 `2087.159 ms`、P95 `4075.593 ms`。

修复前曾有四个失败样本被 `assess` 判为 `missing_fact`：
`gen-open-qingyuan-lantern`、`gen-open-pipa-music`、`gen-open-kongque-tragedy`、
`gen-open-multi-moon`。根因分别是目标事实句未进入有效证据、长诗父级上下文在 1200
字处截断，以及多证据问题未保证两首目标作品同时入选。随后通过领域词典扩展、
多证据候选槽位、父级上下文轮转和 4800 字预算完成修复；`assess` Prompt 未修改。

完整数据契约、分类结果和后续方向见
[开放语料生成层 holdout v1](20260920-open-corpus-generation-evaluation.md)。

## 15. 决策与变更记录

| 日期 | 决策或变化 | 原因 |
| --- | --- | --- |
| 2026-09-20 | 首版只做确定性事实、引用和拒答指标 | 先建立可复现基线，避免模型自评掩盖问题 |
| 2026-09-20 | 评估直接运行 `RagChatGraph.stream()` | 保证评估路径与在线问答路径一致 |
| 2026-09-20 | 每个样本使用独立数据库 Session | 避免前一样本事务和身份状态污染后续样本 |
| 2026-09-20 | 单样本异常记录错误码后继续 | 一份评估报告应能暴露所有失败样本，而不是提前中断 |
| 2026-09-20 | 失败样本按“检索、语料、评估口径、组装”分类定位 | 避免用调 Prompt 掩盖结构缺陷 |
| 2026-09-20 | 开放语料 holdout 从 24/28 修复到 28/28 | 通过证据召回和上下文组装修复，不修改 assess Prompt |
| 2026-09-20 | 历史报告保留原样，不因口径变更回写 | 报告是历史证据，覆盖会破坏可追溯性 |
| 2026-09-20 | 评估与在线问答共用 `build_chat_retrieval` | 证据组装链一旦分叉，评估结论就失去意义 |
| 2026-09-20 | 默认评估集切换为 28 条开放语料 holdout | 12 条种子集无法代表扩库后的生成质量 |

## 16. LLM-as-judge 增量

v3 生成报告生成后，新增 `judge_generation.py` 读取已有 JSON，对 16 条可答样本执行独立
judge，10 条拒答样本按设计跳过。真实结果为 16/16 完成、0 个 judge 错误、忠实度通过率
`0.8125`、回答相关性 `1.0`、claim 支撑率 `0.950920`，没有完全无支撑 claim。

judge 不修改在线图或原始 v3 报告。总体忠实度由 claims 确定性推导，模型只负责拆分事实
声明；引用 rank 按实际证据集合校验。完整设计、失败边界和人工盲评流程见
[生成质量 LLM-as-judge](20260924-generation-judge.md)。
