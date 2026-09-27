# 索引运行元数据

> 状态：基础层已实现；任务控制面和 active index 原子发布已于 2026-09-27 补齐
> 创建日期：2026-09-19
> 最近更新：2026-09-27
> 关联任务：记录一个诗词版本从切块、Embedding 到向量写入的运行生命周期

> 后续实现见 `20260927-index-run-task-control.md` 和
> `20260927-active-index-publication.md`。本文保留索引运行元数据的原始设计，
> 当前 HTTP、租约、重试和取消行为以任务控制面文档及
> `FRONTEND_BACKEND_CONTRACT.md` 为准，发布指针语义以 active index 文档为准。

## 1. 背景与问题

`poem_chunks` 已经能保存版本化切块，但一次索引任务还需要回答：

1. 使用哪个切块策略、Embedding 模型、维度和向量集合。
2. 当前执行到切块、Embedding 还是向量写入阶段。
3. 处理了多少 chunks，是否成功，失败原因是什么。
4. 是谁、何时发起了这次运行，以及运行耗时。

如果没有独立运行记录，后续 Worker 重试、Qdrant 对账、模型切换和问题追踪只能依赖日志，无法在数据库中审计。

## 2. 目标

1. 建立 `poem_index_runs`，关联不可变诗词版本。
2. 记录 `pending -> running -> succeeded/failed` 的基础状态流。
3. 记录 `chunk -> embed -> upsert` 阶段和数量。
4. 保存非敏感的配置快照、模型 ID、向量维度、collection 和错误摘要。
5. 同一版本同一时间只允许一个未结束的索引运行。
6. 删除诗词时级联删除其索引运行记录。

## 3. 非目标

1. 初始阶段不实现 Celery/RQ Worker、HTTP 任务接口或前端进度页；Worker、HTTP
   控制和取消能力已于 2026-09-27 在后续切片补齐。
2. 原始元数据切片不调用 Qwen Embedding，也不连接 Qdrant；后续索引 Worker 已复用
   现有 Embedding 和 Qdrant Provider。
3. 原始元数据切片不定义“当前有效索引”的切换与 Qdrant 清理算法；MySQL pointer
   和 chunk 运行标签已由 2026-09-27 的 active index 切片补齐，旧点 GC 仍未实现。
4. 本功能不实现自动重试、取消、超时回收和运行对账；其中自动重试、租约回收和取消
   已由 2026-09-27 的任务控制面实现，运行对账仍未实现。
5. 不保存 API Key、Token、Cookie、密码或供应商原始请求。

## 4. 用户场景与验收标准

| 场景 | 操作 | 预期结果 |
| --- | --- | --- |
| 创建任务 | 管理员或 Worker 为版本创建索引运行 | 生成 `pending/chunk` 记录，配置快照已脱敏 |
| 开始执行 | Worker 锁定任务并开始切块 | 状态变为 `running`，写入开始时间 |
| 切块完成 | 记录实际 chunk 数 | 阶段进入 `embed`，写入 `chunk_count` |
| Embedding 完成 | 记录实际向量数 | 阶段进入 `upsert`，写入 `embedded_count` |
| 写入成功 | Qdrant upsert 完成 | 状态为 `succeeded`，写入结束时间 |
| 执行失败 | 模型或向量库异常 | 状态为 `failed`，保存截断后的错误摘要 |
| 重复提交 | 同一版本已有未结束任务 | 返回 `409 INDEX_RUN_ALREADY_ACTIVE` |
| 密钥误传 | 配置快照包含敏感字段名 | 值替换为 `[REDACTED]` 后入库 |

## 5. 方案概览

```text
PoemVersion
  -> PoemIndexRun(pending, chunk)
  -> start() -> running
  -> mark_chunks_ready() -> embed
  -> mark_embeddings_ready() -> upsert
  -> succeed()/fail()
```

索引运行是审计和任务编排记录，不是检索事实源。检索最终仍以有效 chunks、向量索引状态和版本可见性为准。自 `20260927_0007` 起，作品的 `active_index_run_id` 决定当前发布运行，chunk 的 `index_run_id` 决定它是否属于该发布。

### 5.1 并发边界

1. 创建运行前对 `poem_versions` 目标行执行 `SELECT ... FOR UPDATE`。
2. 在同一事务中检查该版本是否已有 `pending` 或 `running` 运行。
3. 检查通过后创建 `pending` 运行并提交，版本行锁随事务释放。
4. 该实现防止正常 API/Worker 并发创建重复活跃任务，不代替分布式任务系统的租约和超时回收。

### 5.2 配置快照边界

`config_snapshot` 只保存可复现实验所需的信息，例如：

```json
{
  "chunker": "structural-v1",
  "provider": "qwen",
  "embedding_model": "verified-model-id",
  "batch_size": 16,
  "normalize": true
}
```

写入前递归检查键名。包含 `api_key`、`authorization`、`cookie`、`password`、`secret` 或 `token` 的字段统一写入 `[REDACTED]`。API 凭据只能来自环境变量或密钥服务。

## 6. 接口与契约

当前已实现管理员接口：

```text
GET  /api/v1/admin/index-runs
POST /api/v1/admin/index-runs
GET  /api/v1/admin/index-runs/{run_id}
POST /api/v1/admin/index-runs/{run_id}/retry
POST /api/v1/admin/index-runs/{run_id}/cancel
```

创建和重试返回 `202 Accepted`，创建接口支持 `Idempotency-Key`。当前稳定错误码：

| 错误码 | HTTP | 含义 |
| --- | --- | --- |
| `POEM_VERSION_NOT_FOUND` | 404 | 目标版本不存在 |
| `INDEX_RUN_NOT_FOUND` | 404 | 运行记录不存在 |
| `INDEX_RUN_INVALID_STATUS` | 409 | 当前状态不允许该转换 |
| `INDEX_RUN_ALREADY_ACTIVE` | 409 | 同一版本已有未结束运行 |
| `INDEX_RUN_LEASE_LOST` | 409 | Worker 租约已失效 |
| `INDEX_RUN_ATTEMPTS_EXHAUSTED` | 409 | 已达到最大尝试次数 |
| `TASK_QUEUE_UNAVAILABLE` | 503 | 任务队列未启用或 broker 不可用 |
| `VALIDATION_ERROR` | 422 | 维度或计数不合法 |

## 7. 数据设计

`poem_index_runs` 字段：

```text
id
poem_version_id
status
stage
chunk_strategy
embedding_model
embedding_dimension
vector_collection
config_snapshot
chunk_count
embedded_count
error_message
created_by_id
idempotency_key
celery_task_id
attempt_count
max_attempts
lease_owner
lease_expires_at
heartbeat_at
cancel_requested_at
error_code
started_at
finished_at
created_at
updated_at
```

约束：

1. `poem_version_id` 删除时级联删除运行记录。
2. `created_by_id` 删除时设为 `NULL`，保留审计记录。
3. `status` 使用 `pending`、`running`、`succeeded`、`failed`、`cancelled`。
4. `stage` 使用 `chunk`、`embed`、`upsert`。
5. `embedding_dimension` 有值时必须大于 0。
6. `embedded_count` 必须在 `0..chunk_count` 范围内。
7. `error_message` 最多保留 2000 字符，不能包含供应商密钥或完整请求头。
8. `idempotency_key` 全局唯一，用于安全重发和避免重复创建。
9. Worker 每次领取时原子增加 `attempt_count`，上限为 `max_attempts`。
10. `lease_owner`、`lease_expires_at` 和 `heartbeat_at` 用于防止并发 Worker
    同时写入；取消请求写入 `cancel_requested_at`。

允许同一版本保留多条历史运行。MySQL 不支持部分唯一索引，因此“唯一活跃运行”
仍由创建事务内的应用层锁与检查保证；Worker 阶段额外使用租约和过期租约重领避免
重复执行。

## 8. 后端设计

1. `IndexRunService` 负责状态转换和计数校验。
2. `create_run()` 负责版本存在性、活跃任务冲突和配置脱敏。
3. `start()` 只允许 `pending -> running`。
4. `mark_chunks_ready()` 和 `mark_embeddings_ready()` 只允许 `running`。
5. `succeed()` 只允许从 `running` 进入终态。
6. `fail()` 允许从 `pending` 或 `running` 进入终态。
7. `claim()`、`heartbeat()`、`release_for_retry()` 和 `cancel()` 已实现。
8. `app/tasks/indexing.py` 负责 Celery 执行；每次领取由数据库 `attempt_count`
   决定是否允许重试，Celery 不单独维护业务重试上限。
9. `app/services/task_queue.py` 提供可禁用队列和 Celery 队列；默认关闭。

## 9. 前端设计

不涉及。后续管理页需要展示状态、阶段、计数、模型、collection、开始/结束时间和脱敏错误摘要。

## 10. RAG 与评估

1. 运行记录必须与评估实验编号或配置版本可关联，当前通过 `config_snapshot` 预留。
2. 同一评估对比中的模型、维度、collection 和切块策略必须来自运行记录。
3. 未实际完成 Embedding 和 Qdrant 写入前，不把运行状态写成索引成功。
4. 后续评估报告需记录模型真实 ID、语料版本和评估集版本。

## 11. 测试计划

| 层级 | 覆盖内容 |
| --- | --- |
| 单元/服务测试 | 正常状态流、非法转换、失败信息、计数边界 |
| 集成测试 | 活跃运行冲突、配置脱敏、删除诗词级联 |
| 迁移测试 | 已完成隔离 SQLite `0004` upgrade、downgrade、再 upgrade |
| 契约测试 | 已覆盖 HTTP API 的错误码、状态码和幂等创建 |
| Worker 集成 | 已覆盖成功、自动重试、达到上限失败和取消后跳过 |
| 补偿扫描 | 已覆盖超时 `pending` 重投、过期租约恢复和尝试耗尽失败 |
| E2E | 真实 MySQL `0005 -> 0006 -> 0005 -> 0006` 已验证；队列开启后仍需真实 Qwen/Qdrant Worker 联调 |

## 12. 风险与回滚

1. Worker 租约、过期重领和 Celery Beat 补偿扫描已实现；扫描默认关闭，启用前
   仍需验证独立 Redis、Broker 断连、Worker 重启和队列容量。
2. 应用层并发保护只覆盖共享 MySQL 的正常创建路径，不覆盖绕过 Service 的写入。
3. “当前有效索引”已由 MySQL pointer 定义；运行成功只有在当前版本匹配时才会切换
   pointer，Qdrant 旧点 GC 和跨库对账仍未实现。
4. 迁移只新增表，回滚会删除运行历史，生产执行前必须确认审计数据保留要求。
5. API 先提交运行再入队；如果进程在两次操作之间崩溃，`pending` 运行会由补偿扫描
   重新投递，但恢复时间取决于扫描间隔和宽限期。

## 13. 实施任务

- [x] 功能设计与状态机确认
- [x] 新增 ORM 模型和 `0004` 迁移
- [x] 实现运行生命周期服务
- [x] 增加活跃运行冲突和配置脱敏
- [x] 增加服务与级联测试
- [x] 在隔离 SQLite 验证 `upgrade -> downgrade -> upgrade`
- [x] 在真实 MySQL 升级到 `0004`
- [x] 更新项目说明、契约和开发日志
- [x] 设计并实现 Worker 租约、过期重领和取消
- [x] 增加 HTTP 控制 API、幂等创建和 Celery 队列
- [ ] 增加孤儿任务补偿、监控和告警
- [x] 定义并实现 MySQL active index pointer 切换
- [ ] 实现对账和旧 Qdrant 点清理

## 14. 决策与变更记录

| 日期 | 决策或变化 | 原因 |
| --- | --- | --- |
| 2026-09-19 | 索引运行与 chunk 数据分开建模 | 区分可复用文本切块和一次具体执行记录 |
| 2026-09-19 | 同版本只允许一个未结束运行 | 避免重复 Embedding、重复 upsert 和孤儿向量 |
| 2026-09-19 | 配置快照入库前递归脱敏 | 运行审计不能成为密钥泄露渠道 |
| 2026-09-19 | 暂不定义唯一 active run | 先等待 Worker、Qdrant 对账和原子切换语义 |
| 2026-09-27 | 业务状态继续以 MySQL 为准，Celery 只负责投递 | 避免把 broker 临时状态误当作索引成功 |
| 2026-09-27 | 数据库尝试次数成为唯一重试上限 | 避免 Celery 与数据库双重重试计数 |
| 2026-09-27 | 取消采用协作式跳过 | 外部 Provider 调用无法可靠强制终止 |
| 2026-09-27 | 增加 MySQL active index pointer 与 chunk 运行标签 | 让发布边界可审计，并在事务提交前隔离半成品向量 |
