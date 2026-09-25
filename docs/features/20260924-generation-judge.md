# 生成质量 LLM-as-judge

> 状态：已实现
> 创建日期：2026-09-24
> 最近更新：2026-09-25
> 关联任务：在确定性生成评估之外，增加忠实度、引用支撑度、答案相关性和拒答正确性检查

## 1. 背景与问题

现有生成评估能够验证必需事实、禁用事实、引用精确率/召回率和拒答行为，但仍无法回答：

1. 回答中的事实声明是否真的被所标注的引用支持。
2. 回答是否直接解决用户问题，而不是只提到了相关关键词。
3. 正确拒答是否被稳定识别，且不消耗额外的 judge 调用。

直接修改在线 Prompt 或在线上链路增加自评会增加延迟和失败面，也会让评估逻辑与用户链路
耦合。本轮采用离线增量：读取已经生成的评估报告，对可答样本调用独立 judge，不重新执行
检索或回答生成。

## 2. 目标

- 对已有生成评估报告执行独立的 LLM 质量判定，不重复消耗生成成本。
- 将回答拆成有限数量的可验证事实声明，并检查每个声明的引用支撑状态。
- 输出 `faithfulness_pass_rate`、`answer_relevance_rate`、`claim_support_rate`、
  `average_unsupported_claims` 和拒答正确率。
- 单样本 judge 失败必须隔离，不能中断整份报告。
- 支持导出不带自动评分的 Markdown 人工盲评表。
- 支持从 judge 报告导出空白评分 CSV，并在人工填写后计算一致率和偏严/偏松方向。
- 保持在线 RAG、Prompt、SSE、数据库和公开 API 不变。

## 3. 非目标

- 不重新运行检索、生成或在线 `RagChatGraph`。
- 不把 judge 结果写回用户消息、引用或公开接口。
- 不把 LLM 评分当成人工文学赏析结论。
- 不使用 v3 结果继续调在线实现；v3 仍然是冻结回归集。
- 本轮不引入第二家模型供应商；当前 judge 与生成器同为 `deepseek-chat`，同源偏差仍需
  通过人工盲评校准。

## 4. 用户场景与验收标准

| 场景 | 操作 | 预期结果 |
| --- | --- | --- |
| 有答案样本 | 读取已有生成报告 | 调用一次 judge，生成 claims 和三个核心 verdict |
| 正确拒答 | 读取 `expected=refusal` 样本 | 跳过 judge，直接计入拒答正确率 |
| 空回答或生成错误 | 读取异常样本 | 跳过 judge，不产生伪造评分 |
| judge JSON 非法 | 单样本解析失败 | 记录 `INVALID_JUDGE_RESPONSE`，后续样本继续 |
| 稀疏引用 rank | 证据 rank 为 `1,2,4,5` | 只校验 rank 是否属于实际证据集合，不假设连续编号 |
| 人工复核 | 传入 `--review-output` | 导出问题、回答、证据和空白评分栏，不泄露自动评分 |
| 人工校准 | 导出 CSV、填写分数后重新读取 | 计算人工与 judge 的精确一致率、严格通过一致率和分歧方向 |

## 5. 方案概览

```text
generation_holdout_1000_v3.json
  -> GenerationEvaluationReport
  -> 跳过 refusal / empty / generation error
  -> 构造 question + answer + citation evidence
  -> 独立 DeepSeek JSON judge
  -> claims 校验 + faithfulness 确定性推导
  -> GenerationJudgeReport
  -> 控制台摘要 + JSON 报告 + 人工盲评 Markdown
```

关键取舍：

1. 总体 `faithfulness` 不交给模型自报，而是由 claims 的 `support` 状态确定性推导：
   全部 `supported` 为 `supported`，全部 `unsupported` 为 `unsupported`，其余为
   `partially_supported`。这样避免“claim 部分支持，但总体误报 supported”导致无效报告。
2. claims 最多保留 12 条核心事实，避免长回答把输出预算消耗在重复或过细声明上。
3. judge 输出预算设为 1400 tokens；实际 v3 长回答约 1100 字符，仍保留稳定余量。
4. 证据按引用 rank 截断到 12000 字符，单条最多 2500 字符，防止 Prompt 无界增长。
5. `supported` claim 必须至少关联一个实际存在的引用 rank。

## 6. 接口与契约

不新增或修改 HTTP API，新增离线 CLI：

```powershell
.\.venv\Scripts\python.exe apps\api\scripts\judge_generation.py `
  --json-output data\eval\reports\generation_holdout_1000_v3_judge.json `
  --review-output data\eval\reports\generation_holdout_1000_v3_blind_review.md
```

参数：

| 参数 | 说明 |
| --- | --- |
| `--report` | 输入生成评估 JSON，默认 v3 报告 |
| `--limit` | 只处理前 N 条，用于低成本真实冒烟 |
| `--json-output` | 可选完整 judge JSON 报告 |
| `--review-output` | 可选人工盲评 Markdown |

CLI 只输出模型 ID 和聚合指标，不输出 API Key、完整上游响应或向量内容。

人工校准使用独立 CLI，不调用模型：

```powershell
.\.venv\Scripts\python.exe apps\api\scripts\calibrate_generation_judge.py `
  --export-template data\eval\reports\generation_holdout_1000_v3_calibration.csv `
  --review-output data\eval\reports\generation_holdout_1000_v3_calibration_review.md
```

第一条命令默认只导出 `judge_failure_case_ids` 中仍可校准的 judge 成功样本；
`--scope all` 导出全部 judge 成功样本。人工填写后执行：

```powershell
.\.venv\Scripts\python.exe apps\api\scripts\calibrate_generation_judge.py `
  --review-input data\eval\reports\generation_holdout_1000_v3_calibration.csv `
  --json-output data\eval\reports\generation_holdout_1000_v3_calibration.json `
  --summary-output data\eval\reports\generation_holdout_1000_v3_calibration_summary.md
```

CSV 人工字段：

| 字段 | 取值 | 含义 |
| --- | --- | --- |
| `relevance_0_2` | `0/1/2` | `0=无关`、`1=部分相关`、`2=直接回答` |
| `faithfulness_0_2` | `0/1/2` | `0=无支撑`、`1=部分支撑`、`2=全部有引用支撑` |
| `notes` | 文本 | 可选，记录分歧原因或边界 |

## 7. 数据设计

新增 schema：

```text
app/schemas/generation_judge.py
app/schemas/generation_judge_calibration.py
```

核心结构：

1. `GenerationJudgeClaim`：事实声明、引用 ranks 和支撑状态。
2. `GenerationJudgeAssessment`：相关性、总体忠实度、claims 和简短理由。
3. `GenerationJudgeCaseResult`：保留来源样本、判定状态、错误码、延迟和引用快照。
4. `GenerationJudgeSummary`：总体和分类聚合指标。
5. `GenerationJudgeReport`：来源数据集、生成模型、judge 模型、时间戳和完整结果。
6. `GenerationJudgeHumanReview`：人工相关性、忠实度和备注。
7. `GenerationJudgeCalibrationReport`：逐样本匹配结果和聚合一致性指标。

判定状态：

| 状态 | 含义 |
| --- | --- |
| `judged` | 成功完成 LLM judge |
| `skipped_refusal` | 来源样本要求拒答或实际已拒答 |
| `skipped_empty` | 回答为空 |
| `skipped_error` | 来源生成阶段已有错误 |
| `judge_error` | judge 调用或响应校验失败 |

## 8. 后端设计

主要模块：

```text
app/evaluation/generation_judge.py
app/evaluation/generation_judge_calibration.py
app/schemas/generation_judge.py
app/schemas/generation_judge_calibration.py
apps/api/scripts/judge_generation.py
apps/api/scripts/calibrate_generation_judge.py
apps/api/tests/test_generation_judge.py
apps/api/tests/test_generation_judge_calibration.py
```

职责边界：

1. Evaluator 读取已有报告并隔离每个样本的 judge 失败。
2. Prompt 只发送问题、回答和引用证据快照。
3. Parser 负责 JSON、claims、引用 rank 和总体忠实度一致性校验。
4. CLI 负责真实 Provider、报告写入和资源关闭。
5. 校准模块读取人工 CSV 和 judge JSON，执行纯离线统计，不访问网络或数据库。
6. 在线 `RagChatGraph`、`ChatService` 和前端不引用这些模块。

## 9. 前端设计

不涉及前端页面。人工盲评使用 Markdown 文件，后续可再设计独立评估页面。

## 10. RAG 与评估

指标定义：

| 指标 | 定义 |
| --- | --- |
| `faithfulness_pass_rate` | 已 judge 样本中，总体忠实度为 `supported` 的比例 |
| `answer_relevance_rate` | 已 judge 样本中，相关性为 `relevant` 的比例 |
| `claim_support_rate` | 所有 claims 中，支撑状态为 `supported` 的比例 |
| `average_unsupported_claims` | 每个已 judge 样本平均包含的完全无支撑 claims 数 |
| `refusal_accuracy` | 应拒答样本中，实际正确拒答且无生成错误的比例 |

### 10.1 v3 真实结果

输入：`generation-holdout-1000-v3`，26 条，生成模型 `deepseek-chat`。

| 指标 | 结果 |
| --- | ---: |
| 总样本 / 已 judge | 26 / 16 |
| judge 错误 | 0 |
| 确定性通过率 | 0.961538 |
| 拒答准确率 | 1.000000 |
| 忠实度通过率 | 0.812500 |
| 回答相关性 | 1.000000 |
| claim 支撑率 | 0.950920 |
| 平均完全无支撑 claims | 0.000000 |

人工复核候选：

- `gen-v3-duange-talent`
- `gen-v3-pozhenzi-dream`
- `gen-v3-multi-dufu-life`

三条都表现为“回答相关，但部分解释性概括没有在引用中直接展开”。这说明 judge 对隐含
文学解释采取保守口径，不能直接等同于回答错误；人工校准结果见 10.3。

### 10.2 人工校准协议

校准样本仅包含 judge 状态为 `judged` 的结果；拒答、空回答、生成错误和 judge 错误不会
进入人工评分。默认范围是 `judge_failure_case_ids` 中仍可校准的候选，避免把“自动判定
失败”和“人工认为失败”混为一谈。

严格通过定义：

```text
human_strict_pass = relevance_0_2 == 2 and faithfulness_0_2 == 2
judge_strict_pass = judge.relevance == relevant and judge.faithfulness == supported
```

分歧方向：

| 方向 | 定义 | 解读 |
| --- | --- | --- |
| `match` | 人工与 judge 的严格通过结果相同 | 结论一致，即使某个维度分数不同 |
| `judge_stricter` | 人工通过，judge 不通过 | judge 可能对隐含文学解释过严 |
| `judge_looser` | 人工不通过，judge 通过 | judge 可能忽略了未直接展开的事实 |

当前已生成三条候选的评分模板和聚焦盲评：

- `data/eval/reports/generation_holdout_1000_v3_calibration.csv`
- `data/eval/reports/generation_holdout_1000_v3_calibration_review.md`

填写结果是 `relevance_0_2` 和 `faithfulness_0_2` 的裸数值 `0/1/2`；带标签、全角冒号
或空单元格会被解析器拒绝，以保证校准结论可复现。

### 10.3 首次人工校准结果

2026-09-25 对 3 条候选完成人工盲评，输入与输出为：

- `data/eval/reports/generation_holdout_1000_v3_calibration.csv`
- `data/eval/reports/generation_holdout_1000_v3_calibration.json`
- `data/eval/reports/generation_holdout_1000_v3_calibration_summary.md`

| 指标 | 结果 |
| --- | ---: |
| 可校准样本 | 16 |
| 已复核样本 / 覆盖率 | 3 / 0.187500 |
| 相关性精确一致率 | 1.000000 |
| 忠实度精确一致率 | 0.000000 |
| 严格通过一致率 | 0.000000 |
| `judge_stricter` / `judge_looser` | 3 / 0 |

三条样本的人工评分均为 `2/2`，judge 相关性均为 `relevant`，但忠实度均为
`partially_supported`。因此当前证据支持“judge 对隐含文学解释偏保守”，不支持“judge
总体判定错误”。该批次由 `judge_failure_case_ids` 定向抽取，存在选择偏差。暂不依据 v3
回归集修改 judge Prompt；建立同时覆盖通过与失败样本的 v4 分层校准集后，再决定阈值或
Prompt 调整。

## 11. 测试计划

| 层级 | 覆盖内容 |
| --- | --- |
| 单元测试 | claims 校验、faithfulness 推导、稀疏 rank、错误隔离、盲评导出 |
| 校准测试 | BOM/空行、`0/1/2` 校验、重复/未知样本、覆盖率、精确一致率和偏严/偏松方向 |
| CLI 测试 | 只导出 judge 成功样本，支持候选过滤，不调用模型 |
| Provider 契约 | 只通过 `ChatModelPort` 调用，Fake Provider 可完整测试 |
| 真实冒烟 | 对 2 条长回答执行真实 judge，确认 `judge_errors=0` |
| 全量评估 | 对 v3 26 条执行一次，保存 JSON 和盲评表 |
| 回归测试 | 后续 Prompt 或 schema 修改继续使用同一套单元测试 |

## 12. 风险与回滚

1. 当前 judge 与生成器同模型，存在同源偏差，不能替代独立人工评价。
2. 每个可答样本增加一次模型调用；拒答和生成失败样本不会调用 judge。
3. LLM 输出仍可能缺失 claims、使用非法 rank 或返回非法 JSON，当前按单样本隔离处理。
4. JSON 报告包含回答和引用快照，只保存在本地评估目录，不输出密钥。
5. 回滚只需删除 judge schema、evaluator、CLI、测试和功能文档，不影响在线问答。

## 13. 实施任务

- [x] Schema 与判定状态
- [x] 独立 judge evaluator
- [x] CLI 与人工盲评导出
- [x] 单元测试和错误隔离
- [x] 真实 v3 冒烟与全量评估
- [x] 人工评分 CSV、校准统计和聚焦盲评导出
- [x] 项目文档与开发日志同步

## 14. 决策与变更记录

| 日期 | 决策或变化 | 原因 |
| --- | --- | --- |
| 2026-09-24 | judge 作为离线增量，不进入在线链路 | 避免增加用户请求延迟和在线失败面 |
| 2026-09-24 | 总体 faithfulness 由 claims 推导 | 模型自报与 claim 状态可能冲突，冗余字段不应成为错误源 |
| 2026-09-24 | 引用 rank 按实际集合校验 | 报告中的 rank 可能稀疏，不能用数量假设连续编号 |
| 2026-09-25 | 不依据三条定向失败样本放宽 judge | 当前覆盖率仅 3/16 且存在选择偏差，先建立分层 v4 校准集 |
| 2026-09-24 | 保留人工盲评导出 | LLM judge 只能作为辅助信号，不能替代人工结论 |
| 2026-09-24 | 人工校准默认只覆盖 judge flagged 候选 | 先复核最大分歧点，避免全量人工成本 |
| 2026-09-24 | 校准严格通过要求相关性和忠实度均为 `2` | 任意维度降级都不能算严格通过 |
