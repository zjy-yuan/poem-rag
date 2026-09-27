# Active Index 原子发布

> 状态：已实现，真实库历史数据已回填；旧点 GC 与跨库对账由同日切片补齐
> 创建日期：2026-09-27
> 最近更新：2026-09-27
> 关联任务：让新索引完成写入前不可见，并在 MySQL 事务内原子切换当前发布版本

## 1. 背景与问题

索引任务此前能够完成切块、Embedding 和 Qdrant upsert，但运行成功并不代表线上
检索已经安全切换到新版本。若检索只按作品的当前版本号读取，可能出现：

1. 新版本 chunk 已生成，但向量尚未全部写入，用户已能读到半成品。
2. 旧版本索引任务晚于新版本完成，覆盖作品当前版本对应的可见数据。
3. 索引失败后缺少明确的线上发布状态，无法判断当前应读取哪一次运行产生的 chunk。

本切片把“索引运行成功”和“索引正式发布”分开：运行记录表示执行结果，MySQL
中的发布指针表示线上检索应当读取哪一次运行。

## 2. 目标

1. 在 `poems.active_index_run_id` 保存当前发布运行的 MySQL 指针。
2. 在 `poem_chunks.index_run_id` 记录 chunk 由哪一次运行写入。
3. 只有当前作品版本对应的运行成功完成 upsert 后，才切换发布指针。
4. chunk 标记与发布指针更新在同一个数据库事务中提交。
5. 旧运行晚完成时不能覆盖已经前进到新版本的发布指针。
6. 没有发布指针的历史数据继续使用旧的版本可见性，避免迁移后立即失检。
7. 提供只填补空值的回填脚本、dry-run 和定向回归测试。

## 3. 非目标

1. 不实现 Qdrant alias；MySQL 是发布事实源，Qdrant 只保存可重建向量。
2. 不在发布事务中同步删除旧 Qdrant 点，避免跨网络调用污染数据库事务。
3. 发布事务不删除旧 Qdrant 点；旧向量 GC、集合对账和孤儿点扫描由独立的
   `20260927-index-vector-reconciliation.md` 切片实现。
4. 不改变管理员索引任务 API、Celery 队列开关或在线问答 API。
5. 不覆盖已有 `active_index_run_id` 或 `index_run_id`。

## 4. 用户场景与验收标准

| 场景 | 操作 | 预期结果 |
| --- | --- | --- |
| 新版本尚未发布 | 新版本 chunk 已生成，但对应运行未完成 | 检索继续返回旧 pointer 对应的 chunks |
| 新版本发布完成 | 当前版本运行完成且 upsert 成功 | chunk 标签与 pointer 在同一事务提交，检索切到新 chunks |
| 旧运行晚完成 | 作品已进入新版本后，旧版本运行才成功 | 旧运行记录成功，但 pointer 不移动 |
| 新运行失败 | 当前版本运行失败 | 旧 pointer 保留，线上检索不回退到半成品 |
| 历史数据兼容 | poem 尚无 pointer | 保持旧版本可见性，直到回填或新运行发布 |
| 历史回填 | dry-run 后执行 `--apply` | 只标记空值和空 pointer，不覆盖已有数据 |

## 5. 方案概览

```text
PoemIndexRun
  -> chunk / embed / upsert
  -> transaction:
       update poem_chunks
         vector_id / embedding metadata / index_run_id / status=ready
       update poems
         active_index_run_id = run.id
         only when poems.version_no == run_version.version_no
  -> commit
  -> retrieval reads only chunks whose index_run_id == poem.active_index_run_id
```

关键边界：

1. Qdrant 可以先写入候选向量；只有 MySQL 事务提交后，新索引才对检索可见。
2. 发布指针只向当前作品版本对应的成功运行移动，较旧运行不能通过晚提交抢回发布权。
3. pointer 为空时保留历史兼容路径；pointer 非空后，其他运行生成的 chunk 不再可见。
4. 发布事务不删除旧点。即使旧点短暂保留，也不能被当前 pointer 的过滤条件读取。
5. 旧点 GC 在删除前锁定全部作品发布目标并重新查询 MySQL 引用，避免把刚发布的
   向量误判为孤儿。

## 6. 接口与契约

本切片不增加公开 HTTP 接口，管理员索引任务 API 保持不变。内部契约如下：

1. `ChunkRepository.mark_indexed()` 负责标记 chunks 并尝试切换发布指针。
2. 方法返回 pointer 实际移动的 poem 数；返回 0 表示运行完成但作品已进入更新版本。
3. 索引服务在同一数据库 session 中调用该方法，由调用方统一提交事务。
4. `Poem.active_index_run_id` 与 `PoemChunk.index_run_id` 均不通过 API 直接暴露。
5. 检索仓储统一使用发布过滤条件，词法、dense 和证据上下文查询不能各自实现不同规则。

## 7. 数据设计

迁移：`20260927_0007_add_active_index_pointer.py`

新增字段：

```text
poems.active_index_run_id
poem_chunks.index_run_id
```

约束与索引：

1. `poems.active_index_run_id` 只建普通索引，不建外键，避免
   `poems -> poem_index_runs -> poem_versions -> poems` 形成约束循环。
2. `poem_chunks.index_run_id` 建普通索引后建立外键，运行记录删除时置空。
3. 两者都允许 `NULL`，用于兼容迁移前数据和尚未发布的运行。

历史数据回填脚本：

```powershell
.\.venv\Scripts\python.exe apps\api\scripts\backfill_active_index.py
.\.venv\Scripts\python.exe apps\api\scripts\backfill_active_index.py --apply
```

脚本只处理 pointer 为空的作品，并选择该作品当前版本最近一次成功运行；如果同一版本
存在属于其他运行的已向量化 chunks，则跳过并在输出中报告冲突。

真实库结果：

```text
回填前 dry-run:
poems_total=1008
poems_without_pointer=1008
publishable_poems=1008
chunks_to_tag=12410
tagged_chunks=0
skipped_ambiguous=0

--apply 后复核:
poems_without_pointer=0
publishable_poems=0
chunks_to_tag=0
skipped_ambiguous=0
```

## 8. 后端设计

1. `app/models/poem.py`、`app/models/chunk.py`：增加发布指针和 chunk 运行标签。
2. `app/repositories/chunks.py`：增加 `_published_index_filter()`，并接入词法、dense
   和证据上下文查询。
3. `ChunkRepository.mark_indexed()`：在同一事务内完成 chunk 标记和 pointer 切换。
4. `app/services/indexing.py`：发布数为 0 时记录 warning，但不把旧运行反向标记失败。
5. `apps/api/scripts/backfill_active_index.py`：默认 dry-run，只有 `--apply` 才写入。
6. `app/services/index_reconciliation.py` 和
   `apps/api/scripts/reconcile_index_vectors.py`：提供独立的只读盘点和显式删除入口。

## 9. 前端设计

不涉及。当前管理员任务页仍未实现；后续可以用 `poem_index_runs` 状态和更新后的
作品索引信息展示任务进度，但不需要直接读取内部 pointer。

## 10. RAG 与评估

1. 发布语义保证问答检索不会读取新版本的半成品 chunks。
2. 词法、dense 和上下文证据使用同一发布边界，避免混合检索出现版本交叉。
3. 旧运行晚完成仍保留审计记录，但不会改变当前检索结果。
4. 后续评估必须记录索引运行 ID 或语料版本，避免把不同发布批次的指标混在一起。
5. 旧点 GC 和缺少向量只做对账报告，不在线修复，也不改变当前 pointer 的可见性。

## 11. 测试计划

| 层级 | 覆盖内容 |
| --- | --- |
| 单元/仓储 | pointer 为空的历史兼容、非 active run 不可见 |
| 集成 | 新版本索引完成前不可见、完成后原子切换、旧运行晚完成不覆盖 |
| 索引服务 | chunk `index_run_id`、作品 pointer 和运行终态一致性 |
| 迁移 | 真实 MySQL `0006 -> 0007 -> 0006 -> 0007` 往返 |
| 回填 | dry-run 统计、真实库 `--apply`、二次复核无剩余候选 |
| 对账 | 活跃点保护、终态孤儿候选、未知点、缺失点、删除前引用二次查询 |
| 回归 | lexical、dense、hybrid、证据上下文和索引任务定向测试 |

## 12. 风险与回滚

1. 当前 pointer 切换不内联清理旧 Qdrant 点；独立 GC CLI 默认 dry-run，真实
   `--apply` 删除路径仍待隔离环境验证。
2. 全量对账已经能识别未知点和缺失点，但不会自动修复；极端故障仍需人工判断。
3. 回填脚本按“当前版本 + 最近成功运行”推断历史发布，迁移前应始终先跑 dry-run。
4. 应用回滚应先停止新索引发布；迁移 downgrade 会删除 pointer 和 chunk 标签，
   但不会删除 Qdrant 数据。
5. 不引入 Qdrant alias 的取舍是让发布语义集中在 MySQL；代价是切换后需要独立的
   旧点 GC 流程。

## 13. 实施任务

- [x] 增加 `poems.active_index_run_id` 与 `poem_chunks.index_run_id`
- [x] 增加 `0007` 迁移和真实 MySQL 往返验证
- [x] 在检索仓储统一接入发布过滤
- [x] 在同一事务中标记 chunks 并切换 pointer
- [x] 增加新版本隔离、旧运行晚完成和历史兼容测试
- [x] 增加 dry-run/apply 回填脚本并完成真实库回填
- [x] 增加旧 Qdrant 点 GC 和跨库对账
- [ ] 增加管理端索引状态与发布状态展示

## 14. 决策与变更记录

| 日期 | 决策或变化 | 原因 |
| --- | --- | --- |
| 2026-09-27 | MySQL pointer 作为唯一发布事实源 | 复用现有事务能力，避免跨库原子性问题 |
| 2026-09-27 | `poems.active_index_run_id` 不建外键 | 防止模型关系形成约束循环 |
| 2026-09-27 | 旧运行晚完成不覆盖新版本 pointer | 作品版本前进后，旧索引不能重新成为线上事实 |
| 2026-09-27 | pointer 为空时保留旧可见性 | 兼容迁移前数据，允许分阶段回填 |
| 2026-09-27 | 旧 Qdrant 点不随发布事务同步删除 | 网络失败不应影响 MySQL 发布事务 |
| 2026-09-27 | GC 与发布共用作品行锁，删除前二次查询引用 | 关闭 inspect 后发生新发布的竞态 |
