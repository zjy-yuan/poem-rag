# 1000 首公开语料的独立 v5 Holdout

> 状态：已冻结，已完成一次性检索与生成盲测并转为观察集；未用于在线策略切换
> 创建日期：2026-09-27
> 最近更新：2026-09-27
> 关联任务：为后续检索、领域标注和问答策略提供未观察的泛化集

## 1. 背景与问题

v1 到 v4 的检索 holdout 已参与在线策略、查询扩展和离线重排的观察。继续根据这些
数据集调参会失去泛化证据；如果后续加入意象、情感或题材标签，也需要一批尚未参与
策略选择的问题作为对照。

本切片在公开数据集 `chinese-gushiwen-1000-v2` 上冻结检索与生成两条 v5 holdout：

1. 检索集负责衡量证据召回、排序和拒答边界。
2. 生成集负责衡量事实覆盖、引用和拒答，不直接复用检索集问题。
3. v5 创建后冻结；若根据 v5 的失败样本修复实现，后续验证必须另建 v6。

## 2. 目标

- 新增 `data/eval/retrieval_holdout_1000_v5.json`。
- 新增 `data/eval/generation_holdout_1000_v5.json`。
- 保证数据集内部 ID 和问题唯一。
- 保证 v5 问题与同族全部旧集不相交。
- 保证检索金标准标题不与 v1-v4 复用，生成引用标题不与 v1-v3 复用。
- 保证所有检索选择器和生成引用都能在当前转换语料中定位。
- 将上述约束固化为 pytest 契约测试。

## 3. 非目标

- 不修改 Embedding、分块、查询改写、在线检索、重排或生成策略。
- 不接入新的公开语料，不扩大 1000 首语料规模。
- 不把 v5 结果直接解释为生产泛化能力。
- 不依据 v5 样本调整阈值、权重、词典或 Prompt。
- 不新增公开 HTTP API、前端页面或数据库迁移。

## 4. 用户场景与验收标准

| 场景 | 操作 | 预期结果 |
| --- | --- | --- |
| 检索泛化 | 运行任意检索策略并指定 v5 数据集 | 能输出 46 条聚合报告，且不读取旧集标签 |
| 生成泛化 | 运行生成评估并指定 v5 数据集 | 能输出 26 条答案、引用和拒答报告 |
| 数据契约 | 加载 v5 JSON | Schema 校验通过，ID 和问题唯一 |
| 跨集独立性 | 与旧 holdout 比较问题 | 旧集问题文本不相交 |
| 语料可定位 | 将选择器应用到转换语料 | 每条金标准至少命中一条记录 |

## 5. 方案概览

v5 继续使用现有 `RetrievalEvaluationDataset` 和
`GenerationEvaluationDataset` Schema，不引入平行格式：

```text
公开转换语料
  -> 冻结 retrieval-holdout-1000-v5
  -> 冻结 generation-holdout-1000-v5
  -> 问题、标题和语料定位审计
  -> 现有评估 CLI
```

问题文本、金标准和拒答分类先冻结，再运行策略。策略运行结果只用于记录泛化表现，
不能反向修改 v5。

## 6. 接口与契约

- 不新增 HTTP API。
- 继续使用 `apps/api/scripts/evaluate_retrieval.py` 和
  `apps/api/scripts/evaluate_generation.py` 的 `--dataset` 参数。
- 检索数据集版本：`retrieval-holdout-1000-v5`。
- 生成数据集版本：`generation-holdout-1000-v5`。
- 数据集加载继续复用 `apps/api/app/schemas/evaluation.py` 和
  `apps/api/app/schemas/generation_evaluation.py`。

## 7. 数据设计

### 7.1 检索集

共 46 条：38 条有答案，8 条无答案。

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
| 合计 | 46 |

冻结审计期间修正了一个确定问题：草稿中的独立作者问题复用了 v3 的问题文本
`陆游`。该样本在不改变题目数量和分类覆盖的前提下替换为 `王安石`，并重新审计所有
旧检索集、生成集和当前转换语料。

### 7.2 生成集

共 26 条：16 条要求回答，10 条要求拒答。

| 分类 | 条数 |
| --- | ---: |
| `poem_fact` | 6 |
| `natural_language` | 7 |
| `multi_evidence` | 3 |
| `refusal_cross_domain` | 3 |
| `refusal_missing_entity` | 3 |
| `refusal_missing_attribute` | 4 |
| 合计 | 26 |

## 8. 后端设计

不新增运行时模块。契约测试扩展已有的两个文件：

- `apps/api/tests/test_retrieval_evaluation.py`
- `apps/api/tests/test_generation_evaluation.py`

测试使用相同的 `converted_corpus_records` fixture，不复制语料加载逻辑。

## 9. 前端设计

不涉及。

## 10. RAG 与评估

1. v5 只定义评估事实，不改变任何在线 RAG 策略。
2. 检索指标继续使用现有通过数、Recall@5、MRR、nDCG@5、无答案准确率和延迟。
3. 生成指标继续使用现有答案事实、引用精确率/召回率和拒答指标。
4. 后续如果以标签过滤或标签重排改变检索，必须先跑旧集回归，再单独报告 v5。
5. v5 已作为当前在线策略的一次性盲测运行，运行后转为观察集；不能根据结果修改
   阈值、权重、词典或 Prompt。
6. v5 结果只说明当前策略在这批未观察样本上的泛化表现，不能表述为生产泛化能力。

### 10.1 检索盲测结果

复现命令：

```powershell
.\.venv\Scripts\python.exe apps\api\scripts\evaluate_retrieval.py `
  --dataset data\eval\retrieval_holdout_1000_v5.json `
  --strategy expanded-hybrid `
  --top-k 5 `
  --json-output data\eval\reports\retrieval_holdout_1000_v5_expanded_hybrid_20260927.json
```

使用在线策略 `expanded-hybrid-rrf-v1`，Dense 门槛为 `0.60`。46 条中通过 45 条，
唯一失败为领域内缺属性的 `no-answer-v5-change-location-08`。

| 指标 | 结果 |
| --- | ---: |
| 通过 | 45/46 |
| 通过率 | 0.978261 |
| Recall@5 | 1.000000 |
| nDCG@5 | 0.917031 |
| MRR | 0.888158 |
| 无答案准确率 | 0.875000 |
| 拒答 P/R/F1 | 1.000000 / 0.875000 / 0.933333 |
| 平均延迟 / P95 | 455.984 ms / 962.792 ms |

该结果不触发策略调整。若后续要针对同类缺失属性问题改进检索，必须另建 v6 验证，
不能把 v5 作为新的调参集。

### 10.2 生成盲测结果

复现命令：

```powershell
.\.venv\Scripts\python.exe apps\api\scripts\evaluate_generation.py `
  --dataset data\eval\generation_holdout_1000_v5.json `
  --json-output data\eval\reports\generation_holdout_1000_v5_20260927.json
```

使用真实 `deepseek-chat` 和在线 `RagChatGraph`，并发保持 `1`。26 条中通过 24 条，
拒答 `10/10` 全部正确，引用精确率和召回率均为 `1.0`。

| 指标 | 结果 |
| --- | ---: |
| 通过 | 24/26 |
| 通过率 | 0.923077 |
| 有答案准确率 | 0.875000 |
| 拒答 P/R/F1 | 1.000000 / 1.000000 / 1.000000 |
| 引用精确率 / 召回率 | 1.000000 / 1.000000 |
| 平均延迟 / P95 | 3786.185 ms / 5727.210 ms |
| 平均 TTFT / P95 | 2616.781 ms / 3580.138 ms |
| wall time / 吞吐 | 98445.149 ms / 0.264 cases/s |

两条失败：

1. `gen-v5-huanghelou-farewell`：`departure` 的完整联句未按字面命中。
2. `gen-v5-multi-family`：`youzi` 的完整联句未按字面命中。

两条样本均已作答、未被误拒、没有运行错误，引用召回均为 `1.0`，评估状态均为
`passed/supported`。本轮只记录自动事实匹配边界，不修改 v5、Prompt 或生成逻辑。

## 11. 测试计划

| 层级 | 覆盖内容 |
| --- | --- |
| 契约测试 | 版本、数量、分类、ID 唯一、问题唯一 |
| 独立性测试 | 问题不与同族旧集相交；标题不复用 |
| 语料测试 | 检索选择器和生成引用都能定位 |
| 回归 | 两个评估测试文件全部通过 |
| 门禁 | Ruff、`scripts/verify.ps1` 和 `git diff --check` |

定向验证结果：

```text
35 passed, 2 warnings
```

只读审计结果：

- retrieval v5：46 条，38 answerable，8 no_evidence。
- generation v5：26 条，16 answer，10 refusal。
- 检索 v5 与 v1-v4 问题交集：空。
- 检索 v5 与生成 v1-v5 问题交集：空。
- 生成 v5 与 v1-v3 问题、引用标题交集：空。
- 无效检索选择器和无效生成引用：0。

盲测结果：

- retrieval v5：45/46、Recall@5 `1.0`、nDCG@5 `0.917031`、MRR `0.888158`。
- generation v5：24/26、拒答 `10/10`、引用 P/R `1.0 / 1.0`。
- 失败样本均保留原定义，没有根据盲测结果反向修改数据集或在线策略。

## 12. 风险与回滚

1. 46 条和 26 条仍属于小样本，只适合作为阶段筛选，不代表生产泛化。
2. 标题不相交不等于主题完全不相交；语义重复仍需人工抽样复核。
3. v5 在策略运行后即成为观察集，不能反复用于阈值调参。
4. 若需要根据 v5 修复样本级实现，必须另建 v6 重新验证，不能改写 v5 后继续宣称
   独立泛化。
5. v5 的数据集和设计文件已进入 Git 基线；本次盲测报告需要随收尾提交一同保存。
6. 两条生成失败来自严格原文短语匹配边界，不能直接解释为模型缺少对应文学事实。

## 13. 实施任务

- [x] 建立检索与生成 v5 数据集
- [x] 修正跨集问题复用
- [x] 审计数量、分类、唯一性和跨集独立性
- [x] 审计金标准在转换语料中的可定位性
- [x] 增加 v5 契约测试
- [x] 运行定向测试
- [x] 执行完整验证
- [x] 将 v5 数据集和设计文件纳入版本管理
- [x] 完成一次性检索与生成盲测
- [x] 保存两份真实评估报告

## 14. 决策与变更记录

| 日期 | 决策或变化 | 原因 |
| --- | --- | --- |
| 2026-09-27 | v5 在运行策略前冻结 | 避免根据结果反向调参 |
| 2026-09-27 | 替换重复问题 `陆游` | 保持与 v1-v4 的问题文本独立 |
| 2026-09-27 | v5 不改变在线策略 | 本轮只建立泛化证据，不引入未验证收益 |
| 2026-09-27 | 样本级修复后另建 v6 | 保证泛化集不被调参污染 |
| 2026-09-27 | v5 检索盲测为 45/46 | 记录当前在线策略的未观察泛化表现，不据结果调参 |
| 2026-09-27 | v5 生成盲测为 24/26 | 两条失败均为严格事实短语未命中，拒答与引用指标保持稳定 |
| 2026-09-27 | v5 运行后转为观察集 | 后续样本级修复和策略比较必须使用新的 v6 |
