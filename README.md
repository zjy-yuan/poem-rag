# Poem RAG

诗词知识库与 RAG 问答项目。

当前已建立：

1. FastAPI 异步后端和统一 API 响应契约。
2. MySQL 用户模型、JWT 登录、Token 刷新和管理员权限依赖。
3. Vue 3、TypeScript、Vue Router、Pinia 前端。
4. 诗词浏览、搜索、登录、注册和个人页面。
5. 结构化语料导入、版本快照、结构切块、词法检索和检索评估基线。
6. Qwen Embedding Provider，支持批量、维度、超时和有限重试配置。
7. Qdrant 最小索引闭环，支持 Collection 校验、向量 upsert、chunk 映射和失败补偿。
8. 内部 Dense 检索 Service，支持 Qdrant 召回、元数据过滤和 MySQL 可见性回查。
9. Hybrid RRF 检索 Service，融合查询扩展词法与 Dense 候选并保留来源匹配类型。
10. 内部查询改写与多查询 RRF，支持月亮、思乡和已知作者实体的确定性扩展。
11. 真实 Qdrant 适配器烟测，覆盖临时 Collection、维度校验、upsert、过滤检索和删除。
12. 用户会话、消息、引用快照和会话所有权校验。
13. DeepSeek Chat Provider 与最小 LangGraph 问答流：查询改写、证据检索、LLM 结构化可答性判定、受证据约束生成和回答校验。
14. POST SSE 流式问答、Vue 问答页、引用卡片、停止生成，以及无证据或判定不可答时的稳定拒答。
15. pytest、Ruff、Vitest 和前端生产构建基线。
16. DeepSeek Chat Provider 已完成真实 `deepseek-chat` 流式与 JSON 模式烟测，并完成真实 MySQL + SSE 基础在线联调：有证据问题返回带引用回答，无答案问题稳定拒答且不产生引用。
17. 生成层评估：默认使用 28 条 1000 首独立 holdout 直接运行在线 `RagChatGraph`，支持答案事实、引用精确率/召回率、拒答 P/R/F1 和延迟报告；当前真实 `deepseek-chat` 结果为 `28/28`、拒答 `7/7`、引用召回率 `1.0`、引用精确率 `0.971429`，旧 12 条种子集保持冻结并保留 `12/12` 历史结果。
18. 在线检索为命中的作品追加诗词级父级上下文 chunk（`parent_context`），让行级命中也能读到完整篇章。
19. 固定来源 `aopao/chinese-gushiwen` 已从 100 首扩展到 1000 首分层语料，完成 10 分片转换、跨分片筛选、真实导入、结构切块、Qwen 向量化和 Qdrant 索引。
20. 当前真实库为 1008 首已发布作品、12415 个 chunks 和 12415 个 Qdrant points；原 50 条检索集已在 1000 首候选池上重跑，只作为扩库冲击回归，后续改用独立 holdout 复核策略切换。
21. 独立 1000 首检索与生成 holdout 已完成当前版本的回归复跑：`expanded-lexical-v1` 为 48/50，`expanded-lexical-v1 + Dense + RRF` 为 50/50；生成层为 28/28。在线问答已切换到 Hybrid RRF，并在 Qdrant 或 Embedding 故障时降级到 `expanded-lexical-v1`；该批样本已参与失败诊断，后续只能作为回归集。
22. 第二批独立 holdout v2（检索 46 条、生成 26 条）已完成复核：在线组合从 40/46 提升到 45/46，生成从 24/26 提升到 26/26；Dense 阈值按粒度生效，`poem`/`line` 主文本允许 `0.02` 容差，`note` 无容差。v2 样本同样转为回归集。
23. 在线 RAG 已增加阶段级内部计时和请求级日志，覆盖 `rewrite`、`retrieval`、`assess`、`generation`、`validate`、TTFT 和总耗时；查询扩展默认最多执行 `CHAT_QUERY_VARIANT_LIMIT=8` 个变体，按权重裁剪并保留原查询。内部 `timing` 不进入公开 SSE。
24. 在线检索完成资源复用与批量 Embedding：Embedding Provider 和 Qdrant 客户端改为进程级共享，一次查询的所有变体合并为一次 Embedding 调用，跨变体向量 ID 回查合并为一次 MySQL 查询。同一 v2 回归集下检索仍为 45/46、MRR `0.907895`，平均延迟从 `874.575 ms` 降到 `483.760 ms`、P95 从 `2770.212 ms` 降到 `1239.925 ms`；`evaluate_retrieval.py` 的 `--min-score` 默认改为读取线上 `CHAT_DENSE_MIN_SCORE`。
25. 第三批独立 holdout v3（检索 46 条、生成 26 条）已完成真实复核：在线检索组合为 45/46、Recall@5 `1.0`、MRR `0.929825`，生成层为 25/26、拒答 `10/10`、引用 P/R `1.0 / 1.0`。v3 完成观察后同样冻结为回归集，后续泛化验证必须新建 v4。
26. 生成质量 LLM-as-judge 已作为离线增量实现：读取已有生成报告，对 16 条可答样本执行独立调用，v3 结果为 `judge_errors=0`、忠实度通过率 `0.8125`、回答相关性 `1.0`、claim 支撑率 `0.950920`；拒答样本跳过 judge，并支持导出人工盲评 Markdown、填写评分 CSV 后自动计算人工与 judge 的一致率及偏严/偏松方向。首次定向校准复核 3/16 条，`judge_stricter=3`，说明 judge 对隐含文学解释偏保守，但该高难子集不能代表总体准确率。
27. Retrieval 2.0 离线评估已完成：新增 `nDCG@k`、唯一 Gold 匹配、`EvidenceReranker` 协议和 `expanded-hybrid-rerank-v1`，并冻结 46 条 v4 泛化集。`deterministic-evidence-v1` 在 v4 上为 38/46、Recall@5 `0.828947`、nDCG@5 `0.776326`、MRR `0.757456`，低于不重排基线的 45/46、`1.0`、`0.915410`、`0.885965`，因此拒绝在线启用；Rerank 仅保留为离线实验能力，在线策略仍为 `expanded-hybrid-rrf-v1`。

## 文档入口

建议先阅读：

1. [文档导航与维护规则](docs/README.md)
2. [项目说明与代码导览](docs/PROJECT_GUIDE.md)
3. [开发流程与工程约定](docs/DEVELOPMENT_WORKFLOW.md)
4. [前后端接口契约](docs/FRONTEND_BACKEND_CONTRACT.md)
5. [开发日志](docs/DEVELOPMENT_LOG.md)

接口的实际运行时定义以 FastAPI `/openapi.json` 和 `/docs` 为准，目标契约以
`docs/FRONTEND_BACKEND_CONTRACT.md` 为准。两者不一致时应先修复差异，再继续扩展功能。

## 本地开发

后端：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r apps\api\requirements-dev.txt
.\.venv\Scripts\python.exe -m uvicorn app.main:app --reload --app-dir apps\api
```

前端：

```powershell
pnpm --dir apps/web install
pnpm --dir apps/web dev
```

默认地址：

```text
前端：http://127.0.0.1:5173
后端：http://127.0.0.1:8000
OpenAPI：http://127.0.0.1:8000/docs
```

数据库迁移：

```powershell
cd apps\api
..\..\.venv\Scripts\python.exe -m alembic upgrade head
```

结构化语料导入预检：

```powershell
.\.venv\Scripts\python.exe apps\api\scripts\import_corpus.py `
  --input data\import\example_corpus_v1.json `
  --dry-run
```

`--dry-run` 只校验 JSON 和字段约束，不连接数据库或写入数据。正式导入的 JSON
契约、报告格式和发布语义见
[结构化语料导入设计](docs/features/20260919-open-licensed-corpus-import.md)。

固定来源 1000 首语料验证：

```powershell
.\.venv\Scripts\python.exe apps\api\scripts\convert_chinese_gushiwen.py `
  --input-dir data\raw\aopao-chinese-gushiwen-c2345d0 `
  --limit 1000 `
  --publish

.\.venv\Scripts\python.exe apps\api\scripts\import_corpus.py `
  --input data\import\generated\chinese-gushiwen-1000-v2.json `
  --report data\import\reports\chinese-gushiwen-1000-v2.import.json `
  --rebuild-chunks

.\.venv\Scripts\python.exe apps\api\scripts\index_chunks.py --all-pending
```

`data/raw/` 和 `data/import/generated/` 被 Git 忽略，避免提交第三方正文。本次真实
导入结果为 902 created、98 unchanged、0 failed；真实库达到 1008 首已发布作品、
12415 个 chunks，Qdrant 共 12415 points、1024 维且状态为 `green`。`--publish`
只用于本次验证，正式导入默认仍为草稿。完整来源、10 分片筛选、指标、许可和复现
说明见
[1000 首真实语料扩库](docs/features/20260923-chinese-gushiwen-1000-corpus.md)。

## 基础验证

一键执行本地质量门禁：

```powershell
.\scripts\verify.ps1
```

也可以只执行后端或前端：

```powershell
.\scripts\verify.ps1 -BackendOnly
.\scripts\verify.ps1 -FrontendOnly
```

CI 在 push 和 pull request 时执行等价的后端与前端任务，不调用真实模型或外部数据服务。
需要 `data/import/generated/` 第三方语料的四项 holdout 对照测试在 CI 中会明确跳过；
本地已生成该语料时会执行完整校验。

分项命令如下：

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m ruff check apps\api
.\.venv\Scripts\python.exe apps\api\scripts\import_corpus.py --input data\import\example_corpus_v1.json --dry-run
.\.venv\Scripts\python.exe apps\api\scripts\evaluate_retrieval.py --top-k 5
.\.venv\Scripts\python.exe apps\api\scripts\evaluate_generation.py --help
pnpm --dir apps\web typecheck
pnpm --dir apps\web test
pnpm --dir apps\web build
```

`--top-k` 默认使用 `data/eval/retrieval_open_corpus_v1.json`，即 50 条开放语料
评估集。该集合最初与 100 首语料快照绑定，现已在 1000 首候选池上重跑，只能作为
扩库冲击回归。Top-5 结果：`lexical-baseline-v1` 36/50、
`expanded-lexical-v1` 34/50、`dense-baseline-v1` 37/50、
`hybrid-rrf-v1 + 0.50` 42/50、`hybrid-rrf-v1 + 0.60` 43/50。Dense 和 Hybrid
仍显著改善自然语言与长文本召回，但新增语料明显挤压多证据和领域内拒答；`0.60`
的召回虽然较高，不能仅凭该观察集切为在线策略。完整来源和历史决策见
[1000 首真实语料扩库](docs/features/20260923-chinese-gushiwen-1000-corpus.md)。

独立 1000 首 holdout 随后给出了新的同集比较。Top-5：

| 策略 | 通过 | Recall@5 | MRR | 无答案准确率 | 平均延迟 | P95 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| `expanded-lexical-v1` | 48/50 | 0.952381 | 0.880952 | 1.0 | 399.557 ms | 1082.055 ms |
| `hybrid-rrf-v1` | 40/50 | 0.869048 | 0.735714 | 0.5 | 273.208 ms | 431.425 ms |
| `expanded-lexical-v1 + Dense + RRF` | 50/50 | 1.000000 | 0.887698 | 1.0 | 1006.362 ms | 2944.838 ms |

在线问答现在使用 Hybrid RRF，Dense 分支设置 `CHAT_DENSE_MIN_SCORE=0.60`；Qdrant 或
Embedding 构造失败时直接降级到 `expanded-lexical-v1`，运行期只对
`EMBEDDING_PROVIDER_ERROR` 和 `VECTOR_STORE_ERROR` 降级，其他异常继续抛出。公开
`GET /api/v1/search/evidence` 仍固定使用词法基线。

第二批独立 holdout `retrieval-holdout-1000-v2`（46 条）用于复核上述策略的泛化能力。
Top-5 同集结果：

| 策略 | 通过 | Recall@5 | MRR | 无答案准确率 | 平均延迟 | P95 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| `expanded-lexical-v1` | 38/46 | 0.815789 | 0.763158 | 0.875 | 411.059 ms | 1222.852 ms |
| `hybrid-rrf-v1` | 36/46 | 0.855263 | 0.761842 | 0.500 | 301.742 ms | 572.421 ms |
| `expanded-lexical-v1 + Dense + RRF` | 45/46 | 1.000000 | 0.907895 | 0.875 | 874.575 ms | 2770.212 ms |

修复结构化主题候选、多标题完整作品槽位和粒度感知 Dense 阈值后，在线组合从 40/46
提升到 45/46；唯一剩余失败 `no-answer-v2-xinqiji-office-08` 由在线 `assess` 负责
拒答，检索层未增加样本特例。同批生成 holdout 从 24/26 提升到 `26/26`。完整契约和
失败边界见
[独立 1000 首 holdout v2](docs/features/20260924-independent-1000-holdout-v2.md)。

可观测性收尾后，对同一 v2 回归集再次运行在线组合：检索为 45/46、Recall@5
`1.0`、MRR `0.907895`、平均延迟 `738.465 ms`、P95 `1970.978 ms`；生成为
`26/26`、拒答 `10/10`、引用 P/R `1.0 / 1.0`、平均延迟 `4043.728 ms`、P95
`6566.794 ms`。本轮结果没有降低 v2 质量，但只有单次复跑，不能把延迟变化严格归因于
变体裁剪。内部 `timing` 事件只用于服务端日志，公开 SSE 不包含该事件，`done` 仍严格
保持 `{finish_reason, latency_ms}`。报告见
`data/eval/reports/retrieval_holdout_1000_v2_after_observability.json` 和
`data/eval/reports/generation_holdout_1000_v2_after_observability.json`；设计说明见
[在线 RAG 可观测性](docs/features/20260924-online-rag-observability.md)。

在线检索随后完成资源复用与批量 Embedding：Embedding Provider 和 Qdrant 客户端在应用
启动时创建并共享，一次查询的全部变体合并为一次 Embedding 调用，跨变体向量 ID 回查
合并为一次 MySQL 查询。同一 v2 回归集、同一 `CHAT_DENSE_MIN_SCORE=0.60` 下，检索
仍为 45/46、Recall@5 `1.0`、MRR `0.907895`、无答案准确率 `0.875`，平均延迟从
`874.575 ms` 降到 `483.760 ms`、P95 从 `2770.212 ms` 降到 `1239.925 ms`。生成层
两次复跑均为 `25/26`、拒答 `10/10`、引用 P/R 均为 `1.0`，两次失败样本不同，属于
评估脚本严格短语匹配的边界，而非检索质量回退。`evaluate_retrieval.py` 的
`--min-score` 默认改为读取 `CHAT_DENSE_MIN_SCORE`，不带参数的报告不再与线上口径
脱节。报告见
`data/eval/reports/retrieval_holdout_1000_v2_after_batch_perf_min060.json` 和
`data/eval/reports/generation_holdout_1000_v2_after_batch_perf.json`；设计说明见
[在线 RAG 性能优化](docs/features/20260924-online-rag-performance.md)。

第三批独立 holdout `retrieval-holdout-1000-v3`（46 条）用于复核上述优化在未观察
问题上的表现。Top-5 同集结果：

| 策略 | 通过 | Recall@5 | MRR | 无答案准确率 | 平均延迟 | P95 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| `expanded-lexical-v1` | 44/46 | 0.973684 | 0.890351 | 0.875 | 220.978 ms | 776.836 ms |
| `hybrid-rrf-v1` | 35/46 | 0.815789 | 0.759649 | 0.625 | 266.291 ms | 431.833 ms |
| `expanded-lexical-v1 + Dense + RRF` | 45/46 | 1.000000 | 0.929825 | 0.875 | 519.561 ms | 1280.804 ms |

唯一检索失败 `no-answer-v3-dufu-death-year-08` 是领域内缺属性问题，在线 `assess`
已正确拒答。同批生成 holdout 结果为 25/26、有答案准确率 `0.9375`、拒答 `10/10`、
引用 P/R `1.0 / 1.0`，唯一失败样本缺少“京口瓜洲一水间 / 钟山只隔数重山”这一
距离事实。v3 已经过真实观察，不再用于参数选择，完整契约、失败边界和复现命令见
[独立 1000 首 holdout v3](docs/features/20260924-independent-1000-holdout-v3.md)。

生成层独立 holdout 为 `data/eval/generation_holdout_1000_v1.json`（28 条），
该数据集当前真实结果为 `28/28`：有答案准确率 `1.0`、拒答准确率 `1.0`、引用精确率
`0.971429`、引用召回率 `1.0`，平均延迟 `5687.155 ms`、P95 `9481.742 ms`。
该结果仍是小样本回归集，不能表述为生产泛化能力；多证据生成 P95 已接近 10 秒，
云部署前必须做性能优化。完整指标、失败边界和回滚方式见
[独立 1000 首 holdout](docs/features/20260923-independent-1000-holdout.md)。

Qdrant 与 Qwen Provider 烟测：

```powershell
.\.venv\Scripts\python.exe apps\api\scripts\smoke_qdrant.py
.\.venv\Scripts\python.exe apps\api\scripts\smoke_qwen_embedding.py --text 明月
.\.venv\Scripts\python.exe apps\api\scripts\smoke_deepseek_chat.py
```

Qdrant 烟测始终使用随机临时 Collection 并在结束后清理；Qwen 烟测需要真实
DashScope 网络和有效 Key；DeepSeek 烟测只报告模型、模式、增量片段数、字符数和
耗时，不输出完整回答。三个脚本都不会输出向量内容、上游响应正文或密钥。

验证 `assess` 依赖的非流式 JSON object 输出：

```powershell
.\.venv\Scripts\python.exe apps\api\scripts\smoke_deepseek_chat.py --json
```

2026-09-20 本轮真实烟测结果：流式模式 `model=deepseek-chat`、
`delta_count=41`、`char_count=63`、`elapsed_ms=982.91`；JSON 模式
`model=deepseek-chat`、`delta_count=0`、`char_count=63`、`elapsed_ms=444.78`。
这些结果只证明 Provider 与真实服务已连通，不代表生成质量或端到端问答准确率。

2026-09-20 基础在线联调结果：

1. `请结合诗句说明《静夜思》里明月和思乡的关系。` 返回
   `meta -> retrieval -> delta* -> citation -> done`，耗时 `2527.81 ms`，策略
   `expanded-lexical-v1`，候选 `10` 条、入选 `5` 条，回答包含 `[1]`，消息状态为
   `completed`，持久化引用 `1` 条。
2. `李白的出生地在哪里？` 返回 `meta -> retrieval -> delta -> done`，耗时
   `1074.54 ms`，返回固定拒答文案，引用事件 `0` 条，消息状态为 `completed`，
   持久化引用 `0` 条。

该验收覆盖真实 HTTP/SSE 事件、真实模型调用和 MySQL 持久化；PowerShell 客户端会
缓冲 SSE 响应，浏览器实时逐块渲染仍由前端测试覆盖。

生成层评估骨架：

```powershell
.\.venv\Scripts\python.exe apps\api\scripts\evaluate_generation.py `
  --json-output data\eval\reports\generation_holdout_1000_v1_after_weighting_v2.json
```

该命令直接运行在线 `RagChatGraph`，需要真实 MySQL、Qdrant、Qwen Embedding 和
DeepSeek 配置。默认数据集为 `data/eval/generation_holdout_1000_v1.json`，
共 28 条独立 holdout；输出答案正确率、引用精确率/召回率、拒答 P/R/F1、平均延迟
和 P95。该确定性命令本身不执行 LLM-as-judge；可在报告生成后单独运行质量 judge。
复现旧 12 条种子集时显式传入
`--dataset data\eval\generation_rag_v1.json`。该数据集当前真实结果为 `28/28`、
平均延迟 `5687.155 ms`、P95 `9481.742 ms`；完整指标、修复记录和剩余
风险见
[独立 1000 首 holdout](docs/features/20260923-independent-1000-holdout.md)。

在已有生成报告上执行独立质量 judge：

```powershell
.\.venv\Scripts\python.exe apps\api\scripts\judge_generation.py `
  --json-output data\eval\reports\generation_holdout_1000_v3_judge.json `
  --review-output data\eval\reports\generation_holdout_1000_v3_blind_review.md
```

该命令不会重新检索或生成回答。v3 的 16 条可答样本全部完成 judge，10 条拒答样本按设计
跳过；忠实度通过率 `0.8125`、回答相关性 `1.0`、claim 支撑率 `0.950920`，平均完全
无支撑 claims 为 `0`。完整边界与结果见
[生成质量 LLM-as-judge](docs/features/20260924-generation-judge.md)。

在已有 judge 报告上执行人工校准：

```powershell
.\.venv\Scripts\python.exe apps\api\scripts\calibrate_generation_judge.py `
  --export-template data\eval\reports\generation_holdout_1000_v3_calibration.csv `
  --review-output data\eval\reports\generation_holdout_1000_v3_calibration_review.md
```

默认只导出 `judge_failure_case_ids` 中可校准的候选，当前为 3 条；`--scope all` 可导出
全部 16 条 judge 成功样本。人工在 CSV 的 `relevance_0_2`、`faithfulness_0_2` 中填写
`0/1/2` 后执行：

```powershell
.\.venv\Scripts\python.exe apps\api\scripts\calibrate_generation_judge.py `
  --review-input data\eval\reports\generation_holdout_1000_v3_calibration.csv `
  --json-output data\eval\reports\generation_holdout_1000_v3_calibration.json `
  --summary-output data\eval\reports\generation_holdout_1000_v3_calibration_summary.md
```

该工具完全离线，不调用模型、不修改在线 RAG、SSE、数据库或公开 API。CSV 的人工字段必须
填写裸数值 `0/1/2`，不能写成 `relevance：2` 等标签形式。首次校准已复核 3/16 条 judge
成功样本，覆盖率 `0.187500`：

| 指标 | 结果 |
| --- | ---: |
| 相关性精确一致率 | 1.000000 |
| 忠实度精确一致率 | 0.000000 |
| 严格通过一致率 | 0.000000 |
| judge 偏严 / 偏松 | 3 / 0 |

三条样本的人工评分均为相关性 `2`、忠实度 `2`，而 judge 将忠实度判为
`partially_supported`。因此当前结论只限于“judge 在这组定向失败候选上对隐含文学解释偏
保守”；样本来自 `judge_failure_case_ids`，不能解释为 judge 的总体准确率。暂不根据 v3
回归集直接放宽 judge Prompt，待建立包含通过样本的分层 v4 校准集后再调整。

Qwen 和 Qdrant 均可用后，可执行真实 chunk 索引：

```powershell
.\.venv\Scripts\python.exe apps\api\scripts\index_chunks.py --all-pending
.\.venv\Scripts\python.exe apps\api\scripts\index_chunks.py --version-id 1
```

CLI 默认直接索引已存在的 `structural-v1` chunks，不重建切块；`--all-pending`
按版本处理所有待索引 chunks，每个版本都会写入一条 `poem_index_runs` 记录。

查询改写词典位于 `data/query_expansion/lexicon_v1.json`，加载时会校验版本、概念、
触发词、扩展词和实体重复项。

问答模型通过以下环境变量配置：

```text
DEEPSEEK_API_KEY=
DEEPSEEK_BASE_URL=https://api.deepseek.com
DEEPSEEK_CHAT_MODEL=deepseek-chat
```

未配置有效 Chat Provider 时，问答流接口返回 `503 CHAT_MODEL_NOT_CONFIGURED`，不会
持久化半成品消息。当前仓库默认模型 ID 是 `deepseek-chat`；“DeepSeek 4.1 Flash”的
精确可用模型 ID 仍需通过真实账号和供应商文档确认后再写入配置。
