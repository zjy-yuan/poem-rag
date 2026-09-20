# 诗词结构切块 `structural-v1`

> 状态：进行中  
> 创建日期：2026-09-19  
> 最近更新：2026-09-19  
> 关联任务：把不可变诗词版本转换成可持久化、可索引的多粒度 chunk

## 1. 背景与问题

`poem_chunks` 已经能保存版本化切块，但还没有稳定的切块算法。若直接按固定字符数切分正文，会把短诗、联句和注释切断，导致检索结果丢失出处和上下文。

本切片先实现纯函数式切块器，把文本结构转换成 `ChunkDraft`，再通过独立的版本级服务写入 `poem_chunks`；不调用 Embedding，也不创建 Qdrant collection。这样可以在引入模型和向量库前独立验证边界、行号和持久化幂等。

## 2. 目标

1. 短篇作品保持整篇作为 `poem` chunk。
2. 长文本优先按空行段落和标点边界切分，最后才使用硬字符上限。
3. 每个非空正文行生成一个或多个 `line` chunk，保留原文行号。
4. 每条注释生成一个或多个 `note` chunk，并保留注释 ID 和适用行范围。
5. 输出稳定的 `chunk_index`、规范化文本和内容哈希。
6. 通过单元测试覆盖短诗、长诗、长行、注释、CRLF、空行和非法输入。

## 3. 非目标

1. 不调用 Qwen Embedding、Qdrant 或任何模型。
2. 不自动识别平仄、韵脚、词牌格律或上下阕语义。
3. 不实现相邻 chunk 关系、重叠窗口和上下文扩展。
4. 不实现管理 API 或前端页面。
5. 不估算 Token 数，因为当前尚未验证 Embedding 模型的分词器。

## 4. 用户场景与验收标准

| 场景 | 操作 | 预期结果 |
| --- | --- | --- |
| 短诗 | 传入两行五言绝句 | 生成一个完整 `poem` chunk 和两个 `line` chunk |
| 长诗 | 传入带空行的长文本 | `poem` chunk 优先按段落和长度边界切分 |
| 超长单行 | 传入超过上限且含标点的单行 | 在标点后切分，避免从句子中间硬切 |
| 注释 | 传入赏析和适用行范围 | 生成 `note` chunk，保留 `annotation_id` 和行范围 |
| 空白行 | 传入 CRLF 和空行 | 行号对应原始正文行，不因空行重新编号 |
| 空内容 | 传入空字符串或纯空白 | 抛出明确 `ValueError` |

## 5. 方案概览

```text
不可变版本正文 + 已选注释
  -> 原文非空行解析（保留原始行号）
  -> poem chunks：整篇 / 空行段落 / 长度边界
  -> line chunks：每个正文行 / 标点边界
  -> note chunks：每条注释 / 空行和标点边界
  -> ChunkDraft[]
  -> 后续由持久化服务写入 poem_chunks
```

### 5.1 选择纯函数的原因

1. 切块边界可以脱离 MySQL、模型和向量库单独测试。
2. 后续可以复用同一批输入比较不同策略，不会把实验结果混入业务事务。
3. 持久化、Embedding 失败重试和 Qdrant 清理是独立问题，暂不耦合。

### 5.2 边界规则

1. 先识别非空行，并保留其在原文中的 1-based 行号。
2. `poem` 粒度：全文不超过 `max_poem_chars` 时保留整篇；超长时按空行段落分组，再按长度分组。
3. `line` 粒度：每个正文行单独成块；超长时优先在 `。！？；` 后切分，其次在 `，、` 后切分。
4. `note` 粒度：每条注释单独处理；空行优先分段，再按标点和长度切分。
5. `chunk_index` 在每个粒度内从 0 开始连续编号。
6. `content_hash` 基于实际 `text` 计算，不使用规范化后的文本。

## 6. 接口与契约

本轮不新增 HTTP API。代码契约是：

```python
chunk_poem(
    content: str,
    *,
    annotations: Iterable[AnnotationChunkInput] = (),
    max_poem_chars: int = 480,
    max_line_chars: int = 200,
    max_note_chars: int = 800,
) -> list[ChunkDraft]
```

`ChunkDraft` 字段：

```text
granularity
chunk_index
text
normalized_text
content_hash
line_start
line_end
annotation_id
```

## 7. 数据设计

不新增表、字段或迁移。`ChunkDraft` 与 `poem_chunks` 的对应关系：

| ChunkDraft | poem_chunks |
| --- | --- |
| `granularity` | `granularity` |
| `chunk_index` | `chunk_index` |
| `text` | `text` |
| `normalized_text` | `normalized_text` |
| `content_hash` | `content_hash` |
| `line_start` / `line_end` | 同名字段 |
| `annotation_id` | `annotation_id` |

持久化时由后续服务补充 `poem_id`、`poem_version_id`、`chunk_strategy` 和 `status`。

## 8. 后端设计

新增 `app/services/chunking.py`，只包含纯函数和不可变数据类型：

1. `AnnotationChunkInput` 表示需要进入切块的注释。
2. `ChunkDraft` 表示尚未写入数据库的 chunk。
3. `chunk_poem()` 负责完整切块流程。
4. 私有辅助函数负责解析行、分段、标点切分、规范化和哈希。

新增 `app/services/chunk_catalog.py` 负责版本级持久化：

1. 从 `poem_versions.snapshot.content` 读取不可变正文。
2. 只把状态为 `published` 的注释加入切块输入。
3. 删除该版本尚无向量的旧 chunks 后重新生成，保证重复执行幂等。
4. 所有新 chunk 使用 `chunk_strategy=structural-v1` 和 `status=pending`。
5. 如果已有 `vector_id`，返回 `CHUNKS_ALREADY_INDEXED`，要求先完成 Qdrant 清理。

暂不在 `CatalogService` 中自动调用。原因是需要先确定：

1. 哪些注释状态允许进入索引。
2. 已写入 Qdrant 的旧向量如何清理。
3. 是否需要在发布时同步切块，或由独立任务处理。

## 9. 前端设计

不涉及。

## 10. RAG 与评估

### 10.1 当前策略

`structural-v1` 只利用文本中可观察的结构，不声称已经实现韵脚感知切分：

1. 短诗整篇保留，避免破坏完整语境。
2. 空行被视为段落或上下阕边界。
3. 标点用于减少句子中间截断。
4. 字符上限是兜底，不是首选边界。

### 10.2 后续策略

1. 导入数据增加 `form`、`ci_pattern`、`stanza` 等元数据后，再实现格律感知切分。
2. 接入 Embedding 前记录 tokenizer 的真实 Token 上限。
3. 建立评估集后比较整篇、句级、段落级和重叠窗口的 Recall@k、MRR 与引用准确率。
4. 没有评估结果前，不声称某个切块策略提升了问答准确率。

## 11. 测试计划

| 层级 | 覆盖内容 |
| --- | --- |
| 单元测试 | 短诗、长诗、标点切分、注释、CRLF、空行、非法范围 |
| 集成测试 | 版本级幂等重建、已发布注释筛选、已有向量拒绝重建 |
| 契约测试 | 不涉及 HTTP |
| E2E | 不涉及 |
| 评估 | 后续建立固定问题和金标准证据后执行 |

## 12. 风险与回滚

1. 字符数不是 Token 数，中文与标点的 Token 映射需要模型接入后验证。
2. 没有可靠的韵书或拼音数据时，自动韵脚切分可能产生错误边界，因此本版本不做。
3. 长行按标点切分仍可能破坏特殊文本结构，失败样例需要保留到评估集。
4. 回滚方式是停止调用新切块器；本轮不修改数据库，因此不需要数据迁移回滚。

## 13. 实施任务

- [x] 功能设计与边界确认
- [x] 实现纯函数切块器
- [x] 增加单元测试
- [x] 通过后端全量测试和 Ruff
- [x] 实现版本级幂等持久化服务
- [x] 在真实 MySQL 重建现有版本 chunks
- [x] 回写项目说明、契约和开发日志
- [ ] 设计 Qdrant 向量清理和重新索引语义
- [ ] 接入版本发布或导入任务
- [ ] 建立切块评估基线

## 14. 决策与变更记录

| 日期 | 决策或变化 | 原因 |
| --- | --- | --- |
| 2026-09-19 | 先实现纯函数切块器，不直接写库 | 隔离文本边界问题与数据库、模型和向量索引问题 |
| 2026-09-19 | 暂不做自动韵脚识别 | 当前没有可靠韵书、拼音或格律元数据，避免伪造领域能力 |
| 2026-09-19 | 空行作为段落/上下阕优先边界 | 对导入文本保持可解释，不依赖不可靠的词牌猜测 |
