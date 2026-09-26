# 生成性能与有界并发评估

> 状态：已实现
> 创建日期：2026-09-26
> 最近更新：2026-09-26
> 关联任务：补齐生成评估的 TTFT 和阶段耗时，并验证有界并发对吞吐与单请求延迟的影响

## 1. 背景与问题

在线 `RagChatGraph` 已经发出 `rewrite`、`retrieval`、`assess`、`generation` 和
`validate` 的内部 `timing` 事件，`ChatService` 也会记录 TTFT。但离线生成评估只
汇总总 `latency_ms`，无法回答以下问题：

1. 用户感知的首字延迟是多少，主要由哪个阶段贡献。
2. 多请求同时运行时，吞吐是否提升，延迟是否会因资源竞争而恶化。
3. 当前共享 Provider、Embedding 和 Qdrant 客户端在有限并发下是否保持质量稳定。

因此本轮先建立可重复的性能评估能力，不直接修改生产并发配置。

## 2. 目标

- 生成评估逐样本记录 `ttft_ms` 和五阶段 `stage_timings_ms`。
- 汇总平均值与 P95，包括总延迟、TTFT、检索和生成阶段。
- `GenerationEvaluator` 支持有界并发，默认 `concurrency=1`，结果顺序保持与数据集一致。
- `evaluate_generation.py` 新增 `--concurrency`，非法值在启动时拒绝。
- 在 `generation-holdout-1000-v3` 上实测 `c1` 与 `c4`，同时检查质量、错误、吞吐和延迟。
- 不修改在线 RAG 语义、公开 API、SSE、数据库、前端或生产并发参数。

## 3. 非目标

- 不把评估 CLI 的并发参数接入 FastAPI 服务或反向代理配置。
- 不宣称 `c4` 已具备生产容量结论；单机 26 条样本不能替代正式压测。
- 不做跨进程压测、数据库连接池调优、Qdrant 参数调优或自动扩缩容。
- 不引入模型型 Rerank、缓存、低召回重试或新的外部服务。
- 不把旧生成报告迁移为新文件格式；新增字段必须兼容读取旧 JSON。

## 4. 用户场景与验收标准

| 场景 | 操作 | 预期结果 |
| --- | --- | --- |
| 阶段观测 | 运行单个生成评估样本 | 报告记录总延迟、TTFT 和已知阶段耗时 |
| 串行基线 | 使用 `--concurrency 1` 运行固定数据集 | 质量不变化，wall time 和吞吐可复现记录 |
| 有界并发 | 使用 `--concurrency 4` 运行同一数据集 | 同时运行不超过 4 个样本，结果顺序不变 |
| 非法并发 | 使用 `--concurrency 0` | 命令在调用模型前退出并返回参数错误 |
| 旧报告读取 | judge/calibration 读取不含新字段的报告 | 新字段使用默认值，旧指标仍可解析 |
| 在线服务 | 检查 FastAPI 和 SSE | 不增加公开字段，不改变在线默认并发 |

## 5. 方案概览

### 5.1 事件采集

`GenerationEvaluator` 直接消费在线 `RagChatGraph` 的内部事件：

- 首个非空 `delta` 到达时记录 `ttft_ms`。
- `kind == "timing"` 时，按 `stage` 记录最后一次合法的 `duration_ms`。
- 原有检索、可答性判定、回答、引用和错误隔离逻辑保持不变。

### 5.2 有界并发

`GenerationEvaluator` 构造时校验 `concurrency >= 1`，评估时使用
`asyncio.Semaphore` 限制同时执行的样本数：

```python
semaphore = asyncio.Semaphore(self.concurrency)

async def run_case(case):
    async with semaphore:
        return await self._evaluate_case(case)

results = await asyncio.gather(*(run_case(case) for case in dataset.cases))
```

`asyncio.gather` 保持结果与输入数据集顺序一致，失败仍由单样本错误隔离逻辑转换为
`error_code`，不会中断整份报告。

### 5.3 吞吐口径

- `wall_time_ms`：从开始调度全部样本到最后一个样本结束的墙钟时间。
- `throughput_cases_per_second = total_cases / (wall_time_ms / 1000)`。
- 单请求延迟和 TTFT 继续按每个样本独立统计平均值与 P95。

吞吐提升与单请求延迟变化必须同时报告，不能只选择更有利的指标。

## 6. 接口与契约

新增 CLI 参数：

```text
--concurrency INT   Maximum concurrent evaluation cases. Default: 1
```

报告新增字段及默认值：

| 层级 | 字段 | 默认值 |
| --- | --- | --- |
| case | `ttft_ms` | `null` |
| case | `stage_timings_ms` | `{}` |
| summary | `average_ttft_ms` | `null` |
| summary | `p95_ttft_ms` | `null` |
| summary | `average_stage_timings_ms` | `{}` |
| summary | `p95_stage_timings_ms` | `{}` |
| report | `concurrency` | `1` |
| report | `wall_time_ms` | `null` |
| report | `throughput_cases_per_second` | `null` |

公开 HTTP API 和 SSE 不变。该并发参数只属于离线评估脚本，不代表在线服务已经启用
并发或提高并发上限。

## 7. 数据设计

不涉及 MySQL、Alembic、Qdrant Collection 或语料数据变更。评估报告仍是本地 JSON，
新增字段全部有默认值，旧报告可继续被生成质量 judge 和人工校准脚本读取。

## 8. 后端设计

- `apps/api/app/evaluation/generation.py`：并发限制、TTFT/阶段采集、汇总和格式化。
- `apps/api/app/schemas/generation_evaluation.py`：case、summary 和 report 字段契约。
- `apps/api/scripts/evaluate_generation.py`：`--concurrency` 参数和构造注入。
- `apps/api/tests/test_generation_evaluation.py`：时序事件、并发上限、结果顺序、非法参数
  和旧报告兼容性测试。

## 9. 前端设计

不涉及前端改动。浏览器端继续只接收既有公开 SSE 事件。

## 10. RAG 与评估

### 10.1 质量与吞吐

`generation-holdout-1000-v3`、真实 `deepseek-chat`、在线策略
`expanded-hybrid-rrf-v1`：

| 指标 | `concurrency=1` | `concurrency=4` | 变化 |
| --- | ---: | ---: | ---: |
| 通过 | 25/26 | 25/26 | 不变 |
| 拒答 | 10/10 | 10/10 | 不变 |
| 引用 P/R | 1.0 / 1.0 | 1.0 / 1.0 | 不变 |
| wall time | 96499.338 ms | 40381.079 ms | -58.2% |
| 吞吐 | 0.269 cases/s | 0.644 cases/s | +139.4% |
| 平均延迟 | 3711.359 ms | 5888.418 ms | +58.7% |
| P95 延迟 | 6100.595 ms | 9330.414 ms | +52.9% |
| 平均 TTFT | 2602.745 ms | 4291.510 ms | +64.9% |
| P95 TTFT | 4204.813 ms | 6583.609 ms | +56.6% |

两组都只有 `gen-v3-guazhou-homesick` 失败，说明本轮并发限制没有引入新的质量或引用
错误。`c4` 的吞吐提升约 `2.39x`，但单请求延迟和 TTFT 同时恶化，说明请求重叠能提高
单位时间完成量，却不能降低单用户等待时间。

### 10.2 阶段耗时

平均值：

| 阶段 | `c1` | `c4` |
| --- | ---: | ---: |
| rewrite | 0.153 ms | 0.113 ms |
| retrieval | 816.431 ms | 2119.523 ms |
| assess | 439.035 ms | 450.355 ms |
| generation | 1403.339 ms | 1726.943 ms |
| validate | 0.037 ms | 0.040 ms |

P95：

| 阶段 | `c1` | `c4` |
| --- | ---: | ---: |
| retrieval | 1831.997 ms | 4676.794 ms |
| assess | 823.069 ms | 1007.651 ms |
| generation | 3147.187 ms | 4290.261 ms |

`retrieval` 的 P95 增幅最大，说明并发下共享检索资源、Qdrant 查询和网络往返可能出现
竞争；当前数据只能支持相关性判断，不能单独归因到某一个组件。下一轮需要拆分为
独立检索并发压测和生成 Provider 并发压测。

### 10.3 结论

- 保留有界并发和阶段统计，作为后续性能实验入口。
- 不把 `concurrency=4` 写入在线服务默认配置。
- 单请求延迟优先级高于评估吞吐；容量结论需要独立压测、连接池预算和错误率门槛。
- 新旧报告均能读取；旧报告的 `concurrency` 自动视为 `1`。

报告：

- `data/eval/reports/generation_holdout_1000_v3_performance_c1.json`
- `data/eval/reports/generation_holdout_1000_v3_performance_c4.json`

复现命令：

```powershell
.\.venv\Scripts\python.exe apps\api\scripts\evaluate_generation.py `
  --dataset data\eval\generation_holdout_1000_v3.json `
  --concurrency 1 `
  --json-output data\eval\reports\generation_holdout_1000_v3_performance_c1.json

.\.venv\Scripts\python.exe apps\api\scripts\evaluate_generation.py `
  --dataset data\eval\generation_holdout_1000_v3.json `
  --concurrency 4 `
  --json-output data\eval\reports\generation_holdout_1000_v3_performance_c4.json
```

## 11. 测试计划

| 层级 | 覆盖内容 |
| --- | --- |
| 单元测试 | timing 事件解析、TTFT、阶段平均值/P95、并发参数校验 |
| 并发测试 | 最大同时执行数不超过 2，结果顺序与数据集一致 |
| 兼容测试 | 缺少新字段的旧 JSON 可被 schema 读取并补齐默认值 |
| CLI 测试 | 默认 `concurrency=1`，可解析 `--concurrency 4` |
| 真实评估 | v3 数据集执行 `c1` 和 `c4`，比较质量、吞吐、延迟和错误 |

实际验证结果：

- 生成评估定向测试：`14 passed, 2 warnings`。
- Ruff：`All checks passed!`。
- `scripts/verify.ps1` 使用唯一 pytest basetemp，规避 Windows 陈旧临时目录权限问题；
  统一门禁为后端 `215 passed`、前端 `9 passed`，类型检查和构建通过。
- 真实评估使用 MySQL、Qdrant、Qwen `text-embedding-v4` 和 `deepseek-chat`。

## 12. 风险与回滚

- 26 条样本和单次 `c1/c4` 运行只能证明当前环境下的趋势，不能代表生产容量。
- `c4` 延迟恶化说明共享 Provider 和 `AsyncSession` 的资源边界仍需拆解验证。
- 上游限流、网络波动和本机负载会改变结果，报告必须保留环境、策略和 quality gate。
- 回滚方式：停止传递 `--concurrency`，评估器默认恢复串行；删除新增报告字段或忽略
  新指标即可。在线服务、数据库、公开 API、SSE 和前端均无需回滚。

## 13. 实施任务

- [x] 扩展 case、summary 和 report 性能字段
- [x] 采集 TTFT 和内部阶段耗时
- [x] 实现有界并发并保持结果顺序
- [x] 增加 CLI 并发参数和非法值校验
- [x] 覆盖时序、并发、兼容性和 CLI 测试
- [x] 执行 v3 真实 `c1/c4` 对照并保存报告
- [x] 同步 README、项目导览、功能索引和开发日志
- [ ] 独立验证检索资源与生成 Provider 的跨请求并发边界
- [ ] 在稳定质量门槛下评估缓存、连接池预算和正式压测工具

## 14. 决策与变更记录

| 日期 | 决策或变化 | 原因 |
| --- | --- | --- |
| 2026-09-26 | 先实现评估能力，不修改在线并发配置 | 当前没有可信的并发和资源竞争基线 |
| 2026-09-26 | 默认 `concurrency=1` | 保持旧评估行为可复现，避免意外出网压力 |
| 2026-09-26 | 同时记录吞吐与单请求延迟 | 吞吐提升不能掩盖单用户等待时间恶化 |
| 2026-09-26 | 新增报告字段全部提供默认值 | 保持 judge、校准脚本和旧报告兼容 |
| 2026-09-26 | `c4` 仅作为实验结论保留 | 质量稳定但单请求延迟和 TTFT 明显上升 |
