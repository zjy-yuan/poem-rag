# 在线 RAG 可观测性与查询变体上限

> 状态：已实现
> 创建日期：2026-09-24
> 最近更新：2026-09-24
> 关联任务：补齐在线问答的请求级诊断信息，并限制查询扩展的分支数量

## 1. 背景与问题

在线问答已经接入 `rewrite -> retrieve -> assess -> generate|refuse -> validate`
条件路由，但此前只能看到整次请求的总耗时和最终结果。出现慢请求或拒答异常时，
无法判断时间消耗在查询改写、检索、可答性判定、生成还是引用校验阶段，也无法把
一次日志与具体的会话、助手消息和请求关联起来。

查询扩展会为部分问题生成多个检索变体。变体越多，检索分支和候选融合的工作量越大，
但当前没有统一上限。需要在不破坏原查询优先级的前提下限制分支数量，并保留可审查的
排序规则。

## 2. 目标

- 为在线 RAG 的 `rewrite`、`retrieval`、`assess`、`generation` 和 `validate`
  阶段记录耗时。
- 在流结束日志中记录请求、会话、助手消息、策略、候选数量、判定状态、首字延迟和
  总耗时，支持按请求追踪。
- 增加 `CHAT_QUERY_VARIANT_LIMIT`，按变体权重裁剪查询扩展，同时保留原查询并恢复
  原始查询顺序。
- 保持公开 HTTP、SSE 事件和数据库结构不变。
- 在不降低 v2 回归质量的前提下，为后续性能优化提供基线数据。

## 3. 非目标

- 不接入 Prometheus、OpenTelemetry 或独立指标存储。
- 不把阶段耗时作为公开 SSE 事件返回给浏览器。
- 不实现动态限流、并发检索、Rerank 或基于延迟的自动变体调整。
- 不新增数据库表、迁移或会话字段。

## 4. 用户场景与验收标准

| 场景 | 操作 | 预期结果 |
| --- | --- | --- |
| 正常问答 | 发送一个可回答的流式问题 | 客户端只收到既有 SSE 事件，服务端记录一次完整指标 |
| 不可答问题 | 证据不足或 `assess` 拒绝 | 日志记录 `finish_reason=no_evidence`、判定状态和失败/拒答边界 |
| 查询扩展 | 问题生成多个变体 | 只执行权重最高的前 N 个变体，原查询始终保留 |
| 配置非法 | `max_variants=0` | 构造检索服务时立即抛出 `ValueError` |
| 公开契约 | 检查 SSE `done` | 仍只包含 `finish_reason` 和 `latency_ms`，不出现 `timing` |

## 5. 方案概览

### 5.1 阶段耗时

LangGraph 节点在 `finally` 中通过自定义流写出内部事件：

```json
{
  "kind": "timing",
  "stage": "retrieval",
  "duration_ms": 123.456
}
```

`ChatService` 消费这些事件并聚合为一次请求的指标。`timing` 只存在于图内部事件流，
不会转换为公开 `ChatStreamEvent`，因此不会改变前端协议。

### 5.2 查询变体裁剪

`ExpandedRetrievalService` 接收 `max_variants`。默认值由
`CHAT_QUERY_VARIANT_LIMIT=8` 注入，允许范围为 `1-20`。

变体权重从高到低为：

1. 原始查询、引号字面量：`3.0`。
2. 显式标题：`2.5`。
3. 已知实体：`2.0`。
4. 概念词和长度至少 4 的正文词：`1.5`。
5. 默认变体：`1.0`。
6. 短正文词：`0.5`。

裁剪时先按“权重降序、原顺序稳定排序”选出索引集合，再按原查询顺序输出。这样既限制
分支数量，又不会因为排序改变检索融合的可解释顺序。

## 6. 接口与契约

### 6.1 公开接口

不新增或修改公开 HTTP API。SSE 事件仍为：

```text
meta -> retrieval -> delta* -> citation* -> done/error
```

`done` 事件仍严格保持：

```json
{
  "finish_reason": "stop",
  "latency_ms": 2380
}
```

内部 `timing` 事件不得进入公开 SSE。

### 6.2 配置

| 环境变量 | 默认值 | 范围 | 说明 |
| --- | ---: | ---: | --- |
| `CHAT_QUERY_VARIANT_LIMIT` | `8` | `1-20` | 单次查询扩展最多执行的检索变体数 |

### 6.3 服务端日志

流结束时记录一条 `Chat stream completed` 日志，字段包括：

`request_id`、`conversation_id`、`assistant_message_id`、`status`、
`error_code`、`finish_reason`、`candidate_count`、`selected_count`、`strategy`、
`assessment_status`、`assessment_reason_code`、`query_variant_limit`、
`rewrite_ms`、`retrieval_ms`、`assess_ms`、`generation_ms`、`validate_ms`、
`ttft_ms` 和 `total_ms`。

失败请求使用 warning 级别，完成请求使用 info 级别；日志不包含模型响应正文、密钥
或完整上游错误体。

## 7. 数据设计

不涉及数据库表和迁移。指标只存在于进程日志中，不写入 MySQL。

## 8. 后端设计

- `apps/api/app/ai/graphs/rag.py`：节点计时和内部 `timing` 事件。
- `apps/api/app/services/chat.py`：消费内部事件、记录 TTFT 和请求级日志，继续只发送
  公开 SSE 事件。
- `apps/api/app/services/query_expansion.py`：变体权重、稳定裁剪和参数校验。
- `apps/api/app/core/config.py`：`CHAT_QUERY_VARIANT_LIMIT` 配置校验。
- 查询扩展和检索异常仍沿用原有错误映射；计时逻辑不改变图路由和失败恢复语义。

## 9. 前端设计

不涉及前端改动。前端无需解析 `timing`，也不能依赖服务端日志字段。

## 10. RAG 与评估

在 `retrieval-holdout-1000-v2` 和 `generation-holdout-1000-v2` 上复跑在线组合：

| 指标 | 本轮结果 |
| --- | ---: |
| 检索通过 | 45/46 |
| Recall@5 | 1.000000 |
| MRR | 0.907895 |
| 无答案准确率 | 0.875 |
| 检索平均延迟 | 738.465 ms |
| 检索 P95 | 1970.978 ms |
| 生成通过 | 26/26 |
| 拒答 | 10/10 |
| 引用 P/R | 1.0 / 1.0 |
| 生成平均延迟 | 4043.728 ms |
| 生成 P95 | 6566.794 ms |

唯一检索失败仍是 `no-answer-v2-xinqiji-office-08`，由在线 `assess` 负责拒答。
本轮延迟低于上一版记录，但这是单次环境复跑，不能严格归因于变体裁剪，也不能作为
正式 A/B 结论。

报告：

- `data/eval/reports/retrieval_holdout_1000_v2_after_observability.json`
- `data/eval/reports/generation_holdout_1000_v2_after_observability.json`

## 11. 测试计划

| 层级 | 覆盖内容 |
| --- | --- |
| 单元测试 | 变体上限校验、权重裁剪、原查询保留和原顺序恢复 |
| 契约测试 | 公开 SSE 不含 `timing`，`done` 字段保持严格集合 |
| 集成测试 | 完成日志包含请求关联字段、阶段耗时、TTFT 和总耗时 |
| 真实评估 | v2 检索和生成 holdout 同集复跑 |

实际验证结果：

- 后端全量测试：`176 passed, 3 warnings`。
- Ruff：`All checks passed!`。
- 新增回归覆盖上述变体裁剪、SSE 隐藏和日志字段断言。
- 真实 MySQL、Qdrant、Qwen Embedding 和 `deepseek-chat` 均完成调用。

## 12. 风险与回滚

- 当前指标只有日志，没有聚合、采样或保留策略；高流量下需要再接入指标后端。
- 变体上限过低可能裁掉有用的长尾变体。短期可将配置调到 `20`；完整回滚需在
  `ChatService` 构造检索服务时移除 `max_variants` 参数。
- `timing` 事件仍是内部事件，若未来要公开必须单独评审，避免把内部实现细节变成
  前端兼容负担。
- 回滚不涉及数据库迁移、公开 HTTP 或 SSE 事件，风险局限于内部检索和日志。

## 13. 实施任务

- [x] 增加 `CHAT_QUERY_VARIANT_LIMIT` 配置和 `.env.example`
- [x] 实现按权重裁剪并保留原查询
- [x] 为 RAG 节点发出内部阶段计时事件
- [x] 聚合请求级日志并保持公开 SSE 不变
- [x] 增加单元、契约和集成回归测试
- [x] 在 v2 holdout 上复跑检索和生成评估
- [x] 同步 README、项目导览、接口契约和开发日志
- [ ] 后续接入指标聚合、采样和保留策略

## 14. 决策与变更记录

| 日期 | 决策或变化 | 原因 |
| --- | --- | --- |
| 2026-09-24 | `timing` 只作为内部图事件和服务端日志 | 避免扩大公开 SSE 契约和前端维护面 |
| 2026-09-24 | 默认变体上限设为 `8`，范围 `1-20` | 限制检索扇出，同时保留足够的多查询召回 |
| 2026-09-24 | 裁剪按权重选择、按原顺序执行 | 既优先保留高价值变体，也保持检索顺序可解释 |
| 2026-09-24 | v2 保持 45/46 和 26/26 | 上限 `8` 未造成本轮回归质量下降 |
| 2026-09-24 | 不把单次延迟下降写成因果结论 | 缺少重复运行和正式 A/B 设计 |
