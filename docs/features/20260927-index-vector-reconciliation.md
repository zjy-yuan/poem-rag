# Index 向量对账与旧点 GC

> 状态：已实现；真实库只读 dry-run 和隔离 collection 的真实 `--apply` 删除已通过
> 创建日期：2026-09-27
> 最近更新：2026-09-27
> 关联任务：清理 active index 切换后遗留的 Qdrant 点，并识别 MySQL 与向量库的引用差异

## 1. 背景与问题

Active index 发布后，MySQL 通过 `poems.active_index_run_id` 和
`poem_chunks.index_run_id` 决定哪些 chunks 对线上检索可见。旧 Qdrant 点不会随
发布事务删除，因此重建索引后可能出现以下情况：

1. Qdrant 中存在已经被新 pointer 淘汰、但仍可被存储的旧点。
2. Qdrant upsert 已成功，但 MySQL 在提交发布前失败，留下没有 chunk 引用的点。
3. MySQL 中存在 `vector_id`，但 Qdrant 对应点缺失，需要先识别而不是静默补写。
4. 正在执行 `pending/running` 索引时，不能把尚未提交引用的点误判为孤儿。
5. 缺少来源 payload 的点无法可靠归属，不能直接自动删除。

本切片提供一个默认 dry-run 的运维 CLI，先对账、再删除能够证明未被 MySQL 引用
且不属于活跃运行的旧点。

## 2. 目标

1. 从 MySQL 读取全部 chunk `vector_id` 和 `index_run_id` 引用。
2. 分页读取 Qdrant collection 中全部 point ID 和 payload。
3. 将点分类为 `live`、`delete_candidate`、`protected`、`unknown` 和 `missing`。
4. 只删除 MySQL 未引用、所属运行已经进入终态且经过锁内二次校验的候选点。
5. `pending/running` 运行写入的点必须保护，不能与活跃发布竞争。
6. 无法判断来源的点只报告、不自动删除。
7. MySQL 有引用但 Qdrant 缺失的点只报告，不自动修复。

## 3. 非目标

1. 不增加公开 HTTP 接口或管理端页面。
2. 不引入 Qdrant alias，也不改变 MySQL 作为发布事实源的架构。
3. 不自动重建缺失向量或修复损坏向量。
4. 不删除开启发布指针前没有可靠 payload 的历史点；它们进入 `unknown` 等待人工确认。
5. 不在发布事务中执行网络删除，避免 Qdrant 故障影响 MySQL 提交。
6. 不把当前 CLI 包装为生产调度、告警或定期清理服务。

## 4. 用户场景与验收标准

| 场景 | 操作 | 预期结果 |
| --- | --- | --- |
| 正常盘点 | 执行默认 dry-run | 输出 expected/actual、live、候选、保护、未知和缺失数量，不删除数据 |
| 旧运行旧点 | run 已成功或失败，且没有 chunk 引用该 point | 归类为 `delete_candidate` |
| 活跃运行写入 | run 为 `pending/running` | 归类为 `protected`，不删除 |
| 旧 payload 兼容 | 点没有 `index_run_id`，但有 `poem_version_id` 且版本有活跃运行 | 归类为 `protected`，不删除 |
| 无法识别来源 | 点既无有效运行也无版本，或 payload 缺失 | 归类为 `unknown`，不删除 |
| 删除前出现新引用 | inspect 后、apply 前 MySQL 写入相同 `vector_id` | apply 锁内二次查询发现引用，取消该点删除 |
| MySQL 引用缺失 | chunk 有 `vector_id`，Qdrant 无对应 point | 进入 `missing` 报告，不自动补写 |

## 5. 方案概览

```text
Inspect
  MySQL chunk snapshot
    -> Qdrant point inventory (scroll, 1000/page)
    -> classify by vector_id, index_run_id/status, poem_version_id

Apply
  end inspect snapshot
    -> lock all poem publication rows
    -> re-read vector_id references for delete candidates
    -> delete only candidates still unreferenced
    -> release database locks
```

分类语义：

| 分类 | 判断 | 动作 |
| --- | --- | --- |
| `live` | point ID 被当前 MySQL chunk 引用 | 保留 |
| `delete_candidate` | 运行已终态且没有 MySQL 引用；或旧点所属版本没有活跃运行 | dry-run 只报告；apply 可删除 |
| `protected` | 所属运行为 `pending/running`，或旧点对应版本存在活跃运行 | 保留 |
| `unknown` | 无法从 payload 和 MySQL 归属 | 只报告 |
| `missing` | MySQL 有 `vector_id`，Qdrant inventory 中没有该点 | 只报告 |

## 6. 接口与契约

新增内部端口：

```python
class VectorStoreInventoryPort(Protocol):
    @property
    def collection(self) -> str: ...

    async def list_points(self) -> list[VectorPointSnapshot]: ...
```

`QdrantVectorStore.list_points()` 使用 `scroll` 按 1000 条分页读取，不加载向量，
只读取 point ID 和 payload。

运维 CLI：

```powershell
.\.venv\Scripts\python.exe apps\api\scripts\reconcile_index_vectors.py
.\.venv\Scripts\python.exe apps\api\scripts\reconcile_index_vectors.py --apply
```

1. 默认 dry-run，只输出统计和可选的 point ID 样本。
2. `--apply` 才会删除 `delete_candidate`，并在删除前重新持有发布锁和查询引用。
3. `--sample-size` 控制每类最多打印的 point ID 数量，默认 10。
4. 该 CLI 不改变公开 HTTP API、SSE、数据库迁移或在线检索策略。

## 7. 锁协议

MySQL 是发布和向量引用的事实源。对账删除必须与索引发布使用同一行锁协调：

1. 索引服务在 Qdrant upsert 前对对应 `poems` 行执行 `FOR UPDATE`。
2. 锁一直持有到 chunk `vector_id/index_run_id` 和 `active_index_run_id` 提交。
3. GC 的 apply 阶段先结束 inspect 的数据库快照，再锁定全部 `poems` 行。
4. 锁定后重新查询候选 point ID 是否已被 `poem_chunks.vector_id` 引用。
5. 只有仍然未被引用的候选点在持有锁期间允许发送 Qdrant delete。

这样关闭了“inspect 看到孤儿、删除前新发布已引用该点”的窗口。SQLite 测试能验证
状态机和二次查询行为，但不能证明 MySQL InnoDB 在真实并发下的锁等待和间隙锁行为；
真实库目前也未遇到可安全删除的孤儿点，因此该并发边界仍需后续演练。

## 8. 后端设计

1. `app/ai/providers/vector_store.py`：增加 inventory 端口和 point snapshot。
2. `app/ai/providers/qdrant.py`：增加分页 `list_points()`。
3. `app/services/index_publication.py`：集中提供单作品发布锁和全量发布锁。
4. `app/services/indexing.py`：在 upsert 前获取作品发布锁，并在 payload 中写入
   `index_run_id`。
5. `app/services/index_reconciliation.py`：负责 inspect、分类、apply 和锁内二次查询。
6. `apps/api/scripts/reconcile_index_vectors.py`：提供默认 dry-run 的运维入口。

`inspect()` 只读，不修改数据库或 Qdrant。`apply()` 不修复缺失点，也不处理
`unknown` 或 `protected`。

## 9. 前端设计

不涉及。后续管理端可以展示对账摘要，但不应直接暴露全量 point ID 或提供无确认的
一键删除。

## 10. RAG 与评估

1. 对账只处理索引存储一致性，不改变检索、查询改写、重排、生成或引用规则。
2. 删除旧点不会影响当前 pointer 可见的 chunks；线上检索仍按 MySQL 发布过滤。
3. 缺失向量可能导致 Dense 召回少候选，但当前必须先报告并人工确认，再决定重建范围。
4. 大 collection 的 inventory、锁等待和 Qdrant 删除延迟需要纳入后续容量评估。

## 11. 测试计划

| 层级 | 覆盖内容 |
| --- | --- |
| Qdrant Adapter | `scroll` 分页、payload 读取、异常映射 |
| 对账 Service | live、缺失、终态候选、活跃保护、未知点、旧 payload 活跃版本 |
| 并发安全 | apply 前新引用出现时取消删除 |
| 发布协调 | 索引 upsert 前锁作品、GC 在删除前锁全部发布目标 |
| CLI | 默认 dry-run、`--apply` 显式开启、样本数量边界 |
| 真实联调 | 只读盘点真实 MySQL 与 Qdrant |
| 隔离集成 | 在真实 Qdrant 临时 collection 中验证 apply 删除、活跃保护和清理 |

定向验证结果：索引、发布、Qdrant 和对账相关测试共 `24 passed, 3 warnings`，Ruff
`All checks passed!`。

真实库 dry-run：

```text
mode=dry-run collection=poem_chunks_v1 expected_points=12415 actual_points=12415
live_points=12415 orphan_points=0 delete_candidates=0 protected_points=0
unknown_points=0 missing_points=0 deleted_points=0
```

这证明当时 MySQL 和 Qdrant 的全量点 ID 一一对应。该只读步骤本身没有执行删除，
也没有证明 MySQL 锁争用或大规模 collection 的耗时。

真实 Qdrant 隔离集成测试默认跳过，只有显式开启时才运行：

```powershell
$env:POEM_QDRANT_INTEGRATION='1'
.\.venv\Scripts\python.exe -m pytest `
  apps\api\tests\test_qdrant_reconciliation_integration.py -q
```

该测试使用测试数据库和随机临时 collection，写入 live、stale、protected、unknown
四类点，执行 inspect 和 apply，并断言只删除 stale 点。清理后 Qdrant collection
列表只剩正式 `poem_chunks_v1`。当前结果为 `1 passed`；该测试验证了真实网络删除路径，
仍不证明 MySQL InnoDB 的并发锁争用或正式库的定时调度行为。

## 12. 风险与回滚

1. `list_points()` 当前把全量 point snapshot 放入内存；12415 点可用，更大规模应改为
   分批流式处理或使用临时快照 collection。
2. Qdrant scroll 不是单个事务快照，盘点期间写入可能造成短暂统计偏差；活跃运行保护
   和 apply 二次校验负责避免误删。
3. apply 会持有全部作品行锁，期间新的索引发布和作品更新需要等待；批次过大时可能
   造成明显停顿。
4. `--apply` 已在真实 Qdrant 临时 collection 中验证，但尚未在正式 collection 上
   执行；正式执行前仍需保留 dry-run 证据和人工确认。
5. `unknown` 和 `missing` 不会自动修复，需要后续运营流程明确人工处置规则。
6. 回滚代码不会恢复已经删除的 Qdrant 点；由于删除前提是终态运行且 MySQL 未引用，
   正确性风险受控，但删除前仍必须保留 dry-run 证据。

## 13. 实施任务

- [x] 增加 Qdrant point inventory 和分页读取
- [x] 增加 MySQL/Qdrant 分类与 dry-run 报告
- [x] 增加活跃运行保护
- [x] 增加删除前发布锁和引用二次查询
- [x] 增加运维 CLI 和定向测试
- [x] 完成真实库只读 dry-run
- [x] 在隔离 collection 上验证真实 `--apply` 删除
- [ ] 增加管理端或定时对账任务，并定义告警和保留策略

## 14. 决策与变更记录

| 日期 | 决策或变化 | 原因 |
| --- | --- | --- |
| 2026-09-27 | MySQL 继续作为引用事实源，Qdrant inventory 只用于核对 | 避免把向量库变成发布状态的第二事实源 |
| 2026-09-27 | `active` 运行和未知来源点默认保护 | 无法证明安全时不删除 |
| 2026-09-27 | apply 前锁全部作品并二次查询引用 | 关闭 inspect 与 delete 之间发生新发布的竞态 |
| 2026-09-27 | 缺失点只报告，不自动补写 | 先区分索引故障、collection 漂移和 MySQL 引用错误 |
| 2026-09-27 | 默认 dry-run，`--apply` 显式开启 | 删除是外部存储上的不可逆操作 |
| 2026-09-27 | 增加真实 Qdrant 临时 collection 隔离测试 | 验证删除网络路径且不触碰正式 collection |
