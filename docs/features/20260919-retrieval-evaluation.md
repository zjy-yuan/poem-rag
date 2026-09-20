# 检索评估基线 `retrieval-evaluation-v1`

> 状态：已实现  
> 创建日期：2026-09-19  
> 最近更新：2026-09-19  
> 关联任务：用固定问题与可移植金标准证据比较词法、Dense、混合检索和 Rerank

## 1. 背景与问题

`lexical-baseline-v1` 已能从当前有效版本检索 poem、line 和 note 证据，但仅靠接口烟测只能证明“能返回结果”，不能回答：

1. 正确证据是否出现在 Top-k。
2. 第一个正确证据排在第几位。
3. 无关问题是否被错误召回。
4. 自然语言问题相对精确引用差在哪里。
5. 后续 Qwen Embedding、Qdrant、RRF 和 Rerank 是否真的改善。

因此需要先建立与数据库自增 ID 解耦、可复现、保留失败样例的检索评估集和脚本，再继续接入向量链路。

## 2. 目标

1. 用“问题 + 可移植金标准选择器”定义评估样本。
2. 直接复用 `RetrievalService`，保证评估对象与线上词法检索路径一致。
3. 计算 Recall@k、MRR、Hit Rate、拒答准确率、无结果率、平均延迟和 P95 延迟。
4. 输出分类指标、失败样本和完整召回快照。
5. 为 `lexical-baseline-v1` 固化真实 MySQL 基线，作为后续策略的对照组。
6. 评估不新增 API、数据库表或外部网络依赖。

## 3. 非目标

1. 不在本切片接入 Qwen Embedding、Qdrant、Rerank 或生成模型。
2. 不把当前 6 首种子作品的结果包装成最终语料规模效果。
3. 不计算答案忠实度、引用准确率或人工文学赏析评分。
4. 不将评估集写入数据库；首版以版本化 JSON 文件管理。
5. 不为了指标好看而删除失败样本或修改问题。

## 4. 用户场景与验收标准

| 场景 | 操作 | 预期结果 |
| --- | --- | --- |
| 精确引用 | 查询“床前明月光，疑是地上霜。” | 金标准 line 进入 Top-k |
| 短语 | 查询“举头望明月” | 命中对应诗句 |
| 标题 | 查询“水调歌头” | 命中《水调歌头·明月几时有》 |
| 作者 | 查询“李清照” | 命中其作品 |
| 朝代 | 查询“宋” | 命中宋代作品 |
| 多证据 | 查询“明月” | 同时命中《静夜思》和《水调歌头》 |
| 自然语言 | 查询“李白写月亮的诗句” | 当前词法基线允许失败，但必须记录 |
| 无答案 | 查询“量子纠缠的物理实验” | 不应返回证据 |
| 可移植性 | 更换数据库或自增 ID | 金标准选择器不失效 |
| 可复现性 | 相同代码、语料和数据集重复执行 | 指标与失败列表一致 |

## 5. 方案概览

```text
data/eval/retrieval_lexical_v1.json
  -> RetrievalEvaluationDataset 校验
  -> RetrievalEvaluator 逐条调用 RetrievalService
  -> EvidenceSelector 在召回结果中匹配金标准
  -> 汇总总体指标和分类指标
  -> 控制台摘要 + 可选完整 JSON 报告
```

评估器只依赖 `RetrievalSearchPort` 协议，生产运行注入 `RetrievalService`，测试可注入 Fake，因此普通单测不连接 MySQL，也不调用模型。

## 6. 接口与契约

本切片不新增 HTTP API。

命令行入口：

```powershell
.\.venv\Scripts\python.exe apps\api\scripts\evaluate_retrieval.py --top-k 5
```

可导出完整报告：

```powershell
.\.venv\Scripts\python.exe apps\api\scripts\evaluate_retrieval.py `
  --top-k 5 `
  --json-output data\eval\reports\lexical-baseline-v1-top5.json
```

数据集核心结构：

```json
{
  "version": "lexical-baseline-seed-v1",
  "description": "基于当前种子语料的可移植检索回归集",
  "cases": [
    {
      "id": "exact-jingyesi-01",
      "category": "exact_quote",
      "question": "床前明月光，疑是地上霜。",
      "expected": "evidence",
      "gold_evidence": [
        {
          "poem_title": "静夜思",
          "granularity": "line",
          "text_contains": "床前明月光，疑是地上霜。"
        }
      ]
    }
  ]
}
```

选择器支持：

1. `poem_title`
2. `author_name`
3. `dynasty_name`
4. `granularity`
5. `text_contains`
6. `line_start`、`line_end`

选择器至少需要一个条件；`expected=no_evidence` 时不得填写金标准证据。

## 7. 数据设计

首版数据集位于 `data/eval/retrieval_lexical_v1.json`，包含 27 条样本：

| 分类 | 数量 | 说明 |
| --- | ---: | --- |
| `exact_quote` | 7 | 完整诗句或联句 |
| `phrase` | 5 | 诗内短句 |
| `title` | 3 | 标题或词牌 |
| `author` | 2 | 作者名 |
| `dynasty` | 2 | 朝代 |
| `multi_evidence` | 1 | 同一查询需要多作品证据 |
| `natural_language` | 3 | 现代自然语言表达 |
| `no_answer` | 4 | 应拒绝召回的问题 |

数据不写 `poem_id`、`chunk_id` 或 `version_id`，避免迁移数据库后金标准失效。未来语料扩展时新增数据集版本，不直接改变已经用于对比的历史版本语义。

## 8. 后端设计

新增：

1. `app/schemas/evaluation.py`：数据集、选择器、快照、结果和汇总 Schema。
2. `app/evaluation/retrieval.py`：加载、匹配、执行、指标计算和文本格式化。
3. `apps/api/scripts/evaluate_retrieval.py`：真实数据库命令行入口。
4. `apps/api/tests/test_retrieval_evaluation.py`：选择器匹配和指标算术测试。

指标定义：

| 指标 | 定义 |
| --- | --- |
| Recall@k | 有答案样本中，Top-k 命中金标准数量的平均比例 |
| MRR | 第一个相关证据排名倒数的平均值 |
| Hit Rate@k | 至少命中一个金标准的有答案样本比例 |
| 拒答准确率 | 无答案样本中未返回证据的比例 |
| 无结果率 | 有答案样本中完全没有返回候选的比例 |
| P95 延迟 | 按样本延迟升序后第 95 百分位 |

## 9. 前端设计

本切片不新增前端页面。后续可在开发管理页展示评估运行、指标趋势和失败样例，但首版通过命令行报告保持实验可复现。

## 10. RAG 与评估

真实 MySQL 词法基线结果：

```text
dataset=lexical-baseline-seed-v1
strategy=lexical-baseline-v1 top_k=5
cases=27 passed=24 pass_rate=0.888889
recall_at_k=0.869565 mrr=0.869565 hit_rate_at_k=0.869565
answerable_no_result_rate=0.130435 unanswerable_accuracy=1.000000
average_latency_ms=3.297 p95_latency_ms=3.218
```

分类结果：

| 分类 | 通过率 | Recall@5 | MRR |
| --- | ---: | ---: | ---: |
| 精确引用 | 1.000000 | 1.000000 | 1.000000 |
| 短语 | 1.000000 | 1.000000 | 1.000000 |
| 标题 | 1.000000 | 1.000000 | 1.000000 |
| 作者 | 1.000000 | 1.000000 | 1.000000 |
| 朝代 | 1.000000 | 1.000000 | 1.000000 |
| 多证据 | 1.000000 | 1.000000 | 1.000000 |
| 自然语言 | 0.000000 | 0.000000 | 0.000000 |
| 无答案 | 1.000000 | n/a | n/a |

失败样本：

1. `natural-li-bai-moon-01`：`李白写月亮的诗句`
2. `natural-su-shi-midautumn-01`：`苏轼关于中秋的词`
3. `natural-homesickness-01`：`表达思乡情绪的诗句`

结论：当前词法基线适合作为精确引用、短语和结构化过滤的对照组；它不能解决现代自然语言到诗词意象、作者、标题和主题的语义映射。后续必须先证明查询改写、Dense 或混合检索在同一数据集上改善这些失败，同时不能破坏已经通过的精确类样本。

### 10.1 后续实验：确定性查询改写

在不改变历史词法基线和数据集版本的前提下，新增 `expanded-lexical-v1` 作为内部对照
策略。它使用可审查词典识别月亮、思乡和已知作者实体，保留原查询并对多个变体做
RRF 融合：

```text
dataset=lexical-baseline-seed-v1
strategy=expanded-lexical-v1 top_k=5
cases=27 passed=27 pass_rate=1.000000
recall_at_k=1.000000 mrr=0.978261 hit_rate_at_k=1.000000
answerable_no_result_rate=0.000000 unanswerable_accuracy=1.000000
average_latency_ms=3.769 p95_latency_ms=8.012
```

三条自然语言失败样本均已通过，但该结果只覆盖当前 6 首种子作品。下一步仍需在扩展
开放许可语料上复核规则泛化、无答案拒答和延迟，并比较 Dense、Hybrid 与 Rerank。

## 11. 测试计划

| 层级 | 覆盖内容 |
| --- | --- |
| 单元测试 | 选择器字段匹配、Recall/MRR、拒答和汇总算术 |
| 集成测试 | 评估器复用 `RetrievalService` 的真实数据库运行 |
| 契约测试 | 数据集 Schema、唯一 ID 和有答案/无答案约束 |
| E2E | 本切片不涉及 |
| 评估 | 固定 27 条样本执行 Top-5 报告 |

## 12. 风险与回滚

1. 当前评估集只覆盖 6 首种子作品，不能代表最终语料难度和分布。
2. 词法基线延迟只适用于小规模 `LIKE` 扫描，不能外推到大规模语料。
3. `text_contains` 和标题选择器可能因原文标点、异体字或版本差异失效；后续扩展语料时需要复核金标准。
4. 延迟受本机、MySQL 缓存和数据库连接状态影响，应作为同环境趋势比较，不作为绝对服务 SLA。
5. 回滚方式是停止使用评估脚本；它不新增数据库结构，也不修改线上 API。

## 13. 实施任务

- [x] 设计可移植金标准选择器
- [x] 建立 27 条固定评估样本
- [x] 实现 Recall@k、MRR、Hit Rate、拒答和延迟指标
- [x] 实现分类汇总、失败样本和完整召回快照
- [x] 增加评估器单元测试
- [x] 在真实 MySQL 执行词法基线
- [x] 回写功能索引、项目说明、接口契约和开发日志
- [ ] 扩展开放许可真实语料并复核评估集
- [ ] 用相同数据集比较 Qwen Embedding + Qdrant
- [ ] 比较查询改写、RRF 和 Rerank 的增益与成本

## 14. 决策与变更记录

| 日期 | 决策或变化 | 原因 |
| --- | --- | --- |
| 2026-09-19 | 金标准使用内容选择器，不保存数据库自增 ID | 支持数据库迁移、重建和跨环境比较 |
| 2026-09-19 | 评估器直接复用 `RetrievalService` | 防止评估路径与线上检索路径发生实现漂移 |
| 2026-09-19 | 保留自然语言失败样本 | 这些失败是引入查询改写和向量检索的事实依据 |
| 2026-09-19 | 首版用 JSON 数据集，不建评估表 | 降低未稳定实验阶段的结构和迁移成本 |
