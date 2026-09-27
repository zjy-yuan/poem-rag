# 索引任务控制面

> 状态：已实现，任务队列与补偿扫描默认关闭；active index 发布和旧点对账已补齐
> 创建日期：2026-09-27
> 最近更新：2026-09-27
> 关联任务：为版本切块、Embedding 和 Qdrant 写入提供可查询、可重试、可取消的异步控制面

## 1. 背景与问题

`poem_index_runs` 已经能够记录一次索引运行的状态和阶段，但此前只有 Service
和数据库记录，缺少可执行的异步边界：

1. 管理员无法通过 HTTP 创建、查询、重试或取消索引任务。
2. 应用进程直接执行长时间 Embedding 会占用请求线程，也不适合恢复进程崩溃后的任务。
3. 进程崩溃、网络抖动或 Provider 故障后，需要区分可重试失败和终态失败。
4. 重试次数、租约归属和幂等创建必须在并发 Worker 下保持一致。

本功能把索引执行拆成 API 控制面和 Celery Worker 数据面，但不切换到新的在线索引，
也不改变现有问答检索路径。

## 2. 目标

1. 提供管理员索引任务列表、详情、创建、重试和取消接口。
2. 使用 `Idempotency-Key` 防止重复点击创建多个运行。
3. 通过 Celery + Redis 异步领取任务，任务队列默认关闭。
4. 使用数据库中的 `attempt_count/max_attempts` 作为唯一重试上限。
5. 使用租约和心跳避免两个 Worker 同时修改同一运行。
6. 对过期租约允许重新领取，对取消请求提供协作式停止。
7. 保留现有 `pending/running/succeeded/failed/cancelled` 状态和
   `chunk/embed/upsert` 阶段语义。
8. 扫描长时间未领取的 `pending` 运行和租约过期的 `running` 运行，并重新投递；
   重复消息由数据库原子领取去重。

## 3. 非目标

1. 不实现爬虫、上传接口或导入任务队列。
2. 不实现事务 outbox；当前使用补偿扫描缩小“提交运行后、消息投递前”进程崩溃
   造成的影响。
3. 不把 active index 切换、旧 Qdrant 点清理或跨库对账混入 Worker 状态机；
   原子切换和对账分别由同日 `20260927-active-index-publication.md` 与
   `20260927-index-vector-reconciliation.md` 实现为独立能力。
4. 不新增前端任务管理页面。
5. 不实现生产级队列监控、告警、限流和容器编排。
6. 不把 Celery 任务状态当作业务事实源，业务状态仍以 MySQL 记录为准。

## 4. 用户场景与验收标准

| 场景 | 操作 | 预期结果 |
| --- | --- | --- |
| 创建任务 | 管理员提交版本和可选索引配置 | 返回 `202`，创建 `pending` 运行并入队 |
| 幂等重试创建 | 同一 `Idempotency-Key` 再次提交 | 返回原运行，不重复入队 |
| 查询任务 | 按状态或版本分页查询 | 返回任务状态、阶段、尝试次数、租约和错误摘要 |
| Worker 正常执行 | 领取任务并完成索引 | 状态变为 `succeeded`，清理租约 |
| 可重试失败 | Provider 或向量库暂时失败 | 回到 `pending`，尝试次数加一，等待重新投递 |
| 达到重试上限 | 连续失败直到 `attempt_count=max_attempts` | 状态变为 `failed`，不再重试 |
| 取消任务 | 取消 `pending` 或 `running` 运行 | 状态变为 `cancelled`，Worker 不再写入后续结果 |
| 队列未启用 | 创建或重试任务 | 返回 `503 TASK_QUEUE_UNAVAILABLE`，运行保持失败记录 |

## 5. 方案概览

```text
Admin API
  -> create poem_index_run(pending)
  -> enqueue Celery task
  -> Worker claim(run_id, worker_id, lease)
  -> heartbeat
  -> chunk -> embed -> upsert
  -> succeed
     / release_for_retry -> pending -> Celery retry
     / fail
     / cancel requested -> skipped

Celery Beat
  -> reconcile stale pending / expired running
  -> reset to pending or fail exhausted lease
  -> enqueue again
```

控制面和数据面边界：

1. API 只负责鉴权、参数校验、幂等创建、查询和入队。
2. Worker 负责领取租约、执行索引、续租、重试和写终态。
3. MySQL 保存权威状态，Redis 只作为 Celery broker。
4. Celery task ID 用于运行追踪，不用于判断业务是否成功。
5. API 和补偿扫描通过工作线程调用同步 Celery 发布接口，避免阻塞事件循环。
6. 发布阶段禁用 Celery 任务发布重试和 Kombu 惰性连接重试，broker 不可用时快速
   返回 `503`；Worker 仍保留启动和掉线后的连接恢复能力。幂等创建和补偿扫描负责
   后续重试。

## 6. 接口与契约

管理员接口：

```text
GET  /api/v1/admin/index-runs
POST /api/v1/admin/index-runs
GET  /api/v1/admin/index-runs/{run_id}
POST /api/v1/admin/index-runs/{run_id}/retry
POST /api/v1/admin/index-runs/{run_id}/cancel
```

创建请求示例：

```json
{
  "poem_version_id": 1001,
  "rebuild_chunks": true,
  "chunk_strategy": "structural-v1",
  "embedding_model": "text-embedding-v4",
  "embedding_dimension": 1024,
  "vector_collection": "poem_chunks_v1",
  "max_attempts": 3
}
```

接口规则：

1. `POST /index-runs` 支持 `Idempotency-Key`，重复键返回已有运行。
2. 创建和重试成功返回 `202 Accepted`。
3. `GET /index-runs` 支持 `page`、`page_size`、`status` 和 `poem_version_id`。
4. `retry` 只允许 `failed` 且未达到最大尝试次数的运行。
5. `cancel` 只允许 `pending` 或 `running`；取消是协作式的，不强制终止已经发出的外部调用。
6. 队列未启用、Redis 不可达或入队失败时返回 `503 TASK_QUEUE_UNAVAILABLE`。
7. 补偿扫描没有公开 HTTP 接口，只通过 Celery Beat 或任务函数执行。

错误码：

| 错误码 | HTTP | 含义 |
| --- | --- | --- |
| `POEM_VERSION_NOT_FOUND` | 404 | 目标版本不存在 |
| `INDEX_RUN_NOT_FOUND` | 404 | 运行记录不存在 |
| `INDEX_RUN_INVALID_STATUS` | 409 | 当前状态不允许领取、重试或取消 |
| `INDEX_RUN_ALREADY_ACTIVE` | 409 | 同一版本已有 `pending` 或 `running` 运行 |
| `INDEX_RUN_LEASE_LOST` | 409 | Worker 不再持有有效租约 |
| `INDEX_RUN_LEASE_EXPIRED` | 运行记录 | Worker 租约过期，任务已重投或达到尝试上限 |
| `INDEX_RUN_ATTEMPTS_EXHAUSTED` | 409 | 已达到最大尝试次数 |
| `TASK_QUEUE_UNAVAILABLE` | 503 | 任务队列未启用或 broker 不可用 |

## 7. 数据设计

迁移：`20260927_0006_add_index_run_task_control.py`

在 `poem_index_runs` 上新增：

```text
idempotency_key
celery_task_id
attempt_count
max_attempts
lease_owner
lease_expires_at
heartbeat_at
cancel_requested_at
error_code
```

索引：

1. `idempotency_key` 唯一索引，限制长度 128。
2. `celery_task_id` 普通索引，限制长度 155。
3. `(status, lease_expires_at)` 联合索引，用于过期租约恢复。
4. 补偿扫描复用 `updated_at` 判断 `pending` 是否超过宽限期，不在本阶段新增迁移。

迁移只扩展现有运行表。回滚会删除控制字段和对应任务审计信息，不删除诗词、版本、
chunks 或 Qdrant 数据。

## 8. 后端设计

1. `app/api/v1/admin/index_runs.py`：管理员 HTTP 接口、分页和入队。
2. `app/services/index_runs.py`：创建、领取、心跳、重试、取消、终态转换和租约校验。
3. `app/services/task_queue.py`：定义队列协议，提供 Celery 和禁用两种实现。
4. `app/tasks/celery_app.py`：配置 broker、队列、迟到确认和 Worker 丢失重投。
5. `app/tasks/indexing.py`：执行索引服务，按数据库尝试次数决定重试或终态失败。
6. `app/services/indexing.py`：执行切块、Embedding、Qdrant upsert，并在长步骤中续租。
7. `app/tasks/reconciliation.py`：扫描过期租约和超时 `pending`，恢复租约或回收入队。
8. `app/tasks/celery_app.py`：在显式开启补偿扫描时注册 Celery Beat 周期任务。

重试语义：

1. 每次 Worker 领取时由数据库原子增加 `attempt_count`。
2. 只有 Embedding Provider、向量库和内部错误进入自动重试。
3. Celery `self.retry(max_retries=None)` 不单独限制次数，最终上限由数据库决定。
4. 达到上限后记录 `failed` 和 `error_code`，不再重新投递。

补偿语义：

1. `pending` 超过 `INDEX_TASK_RECONCILE_STALE_SECONDS` 后允许再次投递。
2. `running` 的租约过期且仍有尝试次数时，先原子重置为 `pending`，再重新投递。
3. `running` 的租约过期且尝试次数已耗尽时，直接进入 `failed`，不再制造无效消息。
4. 扫描采用 at-least-once 投递策略；重复消息只有在 `claim` 原子更新成功时才会执行。

## 9. 前端设计

本阶段不新增任务管理页面。后续管理页应按此契约展示：

1. 状态、阶段、尝试次数和最大尝试次数。
2. 创建时间、开始时间、结束时间和心跳时间。
3. 模型、维度、collection 和脱敏错误摘要。
4. 仅对允许的状态展示重试或取消操作。

## 10. RAG 与评估

1. 运行记录关联版本、切块策略、模型、维度和 collection，可用于复现索引实验。
2. 成功仅表示切块、Embedding 和 upsert 已完成，不代表在线检索已切换到该索引。
3. 当前仍以现有 active chunks、Qdrant collection 和问答检索策略为事实源。
4. active index 切换已在 0007 中实现；旧点 GC 已提供默认 dry-run 的独立 CLI，
   但真实删除分支和回滚演练仍需继续验证。

## 11. 配置与运维

| 环境变量 | 默认值 | 说明 |
| --- | --- | --- |
| `INDEX_TASK_QUEUE_ENABLED` | `false` | 是否启用异步索引任务 |
| `CELERY_BROKER_URL` | 空 | 为空时回退 `REDIS_URL` |
| `INDEX_TASK_QUEUE_NAME` | `poem.index` | Celery 队列名 |
| `INDEX_TASK_BROKER_SOCKET_TIMEOUT_SECONDS` | `2` | Redis broker 连接与读写超时秒数 |
| `INDEX_TASK_LEASE_SECONDS` | `900` | Worker 租约秒数 |
| `INDEX_TASK_HEARTBEAT_SECONDS` | `30` | 心跳间隔，必须小于租约 |
| `INDEX_TASK_MAX_ATTEMPTS` | `3` | 默认最大尝试次数 |
| `INDEX_TASK_RETRY_BACKOFF_SECONDS` | `5` | Celery 重试等待秒数 |
| `INDEX_TASK_RECONCILE_ENABLED` | `false` | 是否启用 Celery Beat 补偿扫描 |
| `INDEX_TASK_RECONCILE_INTERVAL_SECONDS` | `60` | 补偿扫描间隔 |
| `INDEX_TASK_RECONCILE_STALE_SECONDS` | `300` | `pending` 重新投递宽限期 |
| `INDEX_TASK_RECONCILE_BATCH_SIZE` | `100` | 单次扫描最多处理的运行数 |

本地独立 Redis 容器使用宿主机 `6380` 时，应设置
`CELERY_BROKER_URL=redis://127.0.0.1:6380/0`；`CELERY_BROKER_URL` 为空时会回退
到 `REDIS_URL`，因此启用队列前必须确认两者指向预期实例。

Windows 本地 Worker 示例：

```powershell
cd apps/api
..\..\.venv\Scripts\celery.exe -A app.tasks.celery_app:celery_app worker `
  --loglevel=INFO --pool=solo --queues=poem.index
```

启用补偿扫描后，需要额外启动 Beat：

```powershell
cd apps/api
..\..\.venv\Scripts\celery.exe -A app.tasks.celery_app:celery_app beat `
  --loglevel=INFO
```

## 12. 测试计划

| 层级 | 覆盖内容 |
| --- | --- |
| API | 鉴权、队列关闭、幂等创建和重试入队 |
| 服务 | 领取、心跳、过期租约、重试上限、取消和终态转换 |
| Worker | 成功执行、自动重试、达到上限失败和取消后跳过 |
| 补偿扫描 | 超时 `pending` 重投、过期租约恢复、尝试耗尽失败和队列关闭短路 |
| 迁移 | `0005 -> 0006 -> 0005 -> 0006`，真实 MySQL 已通过 |
| 回归 | 现有索引、检索、问答和前端门禁不回归 |

定向验证：

```powershell
.\.venv\Scripts\python.exe -m pytest `
  apps\api\tests\test_task_queue.py `
  apps\api\tests\test_index_task_queue.py `
  apps\api\tests\test_index_runs.py `
  apps\api\tests\test_indexing.py -q
```

结果：`32 passed, 2 warnings`。

真实 Redis/Celery 验证：

1. Worker 未启动时发布消息，`poem.index` 队列深度为 `1`；Worker 启动后自动消费，
   队列深度恢复为 `0`。
2. 停止独立 Redis 后，入队约 `2.83s` 返回
   `503 TASK_QUEUE_UNAVAILABLE`，没有出现默认 5 到 7 秒重试链。
3. Worker 使用 `redis://127.0.0.1:6380/0`，补偿任务成功接收并返回
   `status=disabled`。

完整门禁 `.\scripts\verify.ps1` 通过：Ruff 通过，后端 `270 passed, 3 warnings`，
前端 typecheck、Vitest `9 passed` 和生产构建通过。

## 13. 风险与回滚

1. API 先提交运行再入队；若进程在两次操作之间崩溃，记录可能短暂停留在 `pending`，
   由补偿扫描重新投递，但恢复时间为扫描间隔加宽限期。
2. 补偿扫描采用 at-least-once，可能产生无效或重复消息；Redis 中会保留短时重复，
   但不会重复执行已经成功领取的运行。
3. 取消是协作式的，无法中断已经发出的模型或网络请求。
4. 队列默认关闭；已在独立 Redis 验证消息持久化、Worker 重启恢复和 broker
   不可达快速失败，生产启用前仍需验证 Worker 容量、告警和长期连接抖动。
5. 回滚可关闭 `INDEX_TASK_QUEUE_ENABLED` 并降级迁移；已执行的外部向量写入不自动回滚。
6. active index 原子切换和旧 Qdrant 点对账已由独立切片补齐，但都不属于 Worker
   状态机的职责；生产调度、告警和真实删除演练仍待完成。

## 14. 实施任务

- [x] 设计 API、状态机、幂等和租约
- [x] 扩展 `poem_index_runs` 和迁移
- [x] 实现管理员查询、创建、重试和取消接口
- [x] 接入 Celery + Redis 队列
- [x] 实现 Worker 领取、心跳、重试和取消跳过
- [x] 增加定向 API、服务和 Worker 测试
- [x] 在真实 MySQL 验证 `0005 -> 0006 -> 0005 -> 0006`
- [x] 增加孤儿任务补偿和过期租约恢复
- [x] 增加 broker 快速失败、发布重试边界和事件循环隔离
- [ ] 增加生产监控、告警和队列容量验证
- [x] 设计 active index 切换并接入 MySQL pointer 发布
- [x] 实现对账和旧点清理

## 15. 决策与变更记录

| 日期 | 决策或变化 | 原因 |
| --- | --- | --- |
| 2026-09-27 | 业务状态以 MySQL 为准，Celery 只负责投递 | 避免把 broker 临时状态误当作索引成功 |
| 2026-09-27 | 数据库尝试次数作为唯一重试上限 | 避免 Celery 和数据库双重重试计数 |
| 2026-09-27 | API 使用幂等键防重复创建 | 安全处理重试请求和客户端超时重发 |
| 2026-09-27 | 取消采用协作式跳过 | 外部 Provider 调用无法可靠强杀 |
| 2026-09-27 | 使用补偿扫描而不是立即引入 outbox | 能以更小复杂度覆盖进程崩溃和 Worker 失联，重复投递由原子领取保证安全 |
| 2026-09-27 | 发布任务时禁用 Celery 内部重试并设置 socket 超时 | 避免 broker 故障长期占用 API 请求和事件循环，由上层幂等接口或补偿扫描重试 |
| 2026-09-27 | 发布连接设置为单次尝试，Worker 启动重试保持开启 | 同时满足 API 快速失败和 Worker 可恢复启动 |
| 2026-09-27 | 队列默认关闭 | 先把控制面做正确，再验证部署和容量 |
| 2026-09-27 | active index 使用 MySQL pointer，不引入 Qdrant alias | 复用数据库事务，避免跨库发布状态不一致 |
| 2026-09-27 | 对账和 GC 保持独立运维 CLI，不混入索引任务状态机 | 任务执行成功与数据存储治理是不同故障域 |
