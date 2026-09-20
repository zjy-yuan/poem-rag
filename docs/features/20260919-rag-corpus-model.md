# RAG 语料数据基础

> 状态：进行中  
> 创建日期：2026-09-19  
> 最近更新：2026-09-19  
> 关联任务：为多粒度切块、检索、引用和评估建立稳定数据基础

## 1. 背景与问题

当前系统已经可以保存和发布诗词，但 `poems` 只表示作品当前状态，缺少以下 RAG 必需信息：

1. 一首作品来自哪个网站、文件或人工录入。
2. 某个版本的正文、作者、摘要和状态快照是什么。
3. 注释、译文、赏析、典故和创作背景如何与作品或具体行关联。
4. 切块结果属于哪个版本，使用什么粒度和策略。
5. 向量重建时如何找到稳定输入，回答时如何回到原文和来源。

如果直接对 `poems.content` 做切块和向量化，后续编辑正文、多个来源版本并存或重新索引时无法判断旧向量是否有效，也无法提供可信引用。

## 2. 目标

1. 建立 `poem_sources`，保存规范化作品与来源之间的可追溯关系。
2. 建立不可变 `poem_versions`，保存作品在某个版本号下的完整规范化快照。
3. 建立 `poem_annotations`，保存注释、译文、赏析、背景和典故。
4. 建立 `poem_chunks`，保存 poem、line、note 三种粒度的切块及其版本归属。
5. 现有诗词创建、编辑、归档和恢复操作自动生成版本快照。
6. 通过 Alembic 迁移为已有诗词补一条当前版本快照。

## 3. 非目标

1. 不实现爬虫、文件导入任务或来源审核页面。
2. 不调用 Qwen Embedding，不连接 Qdrant，不创建向量集合。
3. 不实现切块算法，只定义切块结果和约束。
4. 不实现混合检索、Reranker、LangGraph 或问答接口。
5. 不实现会话、消息、引用、反馈和评估表。
6. 不把大段原始 HTML 存入 MySQL，后续使用对象存储保存。

## 4. 用户场景与验收标准

| 场景 | 操作 | 预期结果 |
| --- | --- | --- |
| 记录来源 | 从人工、文件或未来爬虫产生诗意数据 | 可保存来源类型、来源键、外部 ID、URL、原始字段、许可和抓取时间 |
| 编辑作品 | 管理员修改正文、标题、作者、摘要、分类或标签 | 作品版本号递增，并生成与该版本号对应的不可变快照 |
| 归档恢复 | 管理员软删除后恢复作品 | 每次状态变化递增版本号并生成快照 |
| 保存注释 | 导入一条注释或赏析 | 注释关联到作品版本，可标记适用行范围 |
| 生成 chunk | 后续切块服务处理一个版本 | chunk 记录粒度、顺序、正文、行范围、策略和索引状态 |
| 重建索引 | 后续索引服务读取语料 | 只读取指定版本和状态的 chunks，不依赖 `poems` 当前内容 |

本切片完成后的验收标准：

1. MySQL 可以从 `20260919_0002` 升级到新迁移且可回滚。
2. 已有诗词在升级后各有一条版本快照。
3. 新建、编辑、归档和恢复诗词都会生成对应版本快照。
4. 来源、版本、注释和 chunk 的 ORM 关系及唯一约束有自动化测试。
5. 旧的用户、权限、诗词 CRUD 和目录接口测试保持通过。

## 5. 方案概览

```text
人工 / 文件 / 爬取 / 其他导入
  -> poem_sources
  -> poems 当前规范化作品
  -> poem_versions 不可变规范化快照
       -> poem_annotations
       -> poem_chunks（poem / line / note）
            -> 后续 Embedding 与 Qdrant 索引
```

### 5.1 关键设计

1. **来源与爬取配置解耦**：`poem_sources` 记录数据来源，不依赖尚未实现的爬虫配置表。未来 `crawl_items` 审核后可以写入该表。
2. **来源键稳定**：使用 `source_key + external_id` 识别来源记录，例如 `manual:null` 或 `chinese-poetry:12345`。
3. **版本不可变**：`poem_versions` 创建后不修改，正文变化生成新版本；chunk 始终指向明确版本。
4. **注释归版本**：注释可能随正文版本变化，因此关联 `poem_version_id`，而不是只关联当前作品。
5. **chunk 多粒度共存**：同一版本可以同时生成 poem、line、note 三类 chunks。
6. **向量字段后置**：本切片预留 `vector_id`、`embedding_model` 和 `embedding_dimension`，但不写向量数据。

## 6. 接口与契约

本切片不新增外部 HTTP API，只更新数据设计契约。后续需要以下管理能力，但不属于本轮：

1. 来源列表、详情和人工维护。
2. 版本列表、快照对比和回滚。
3. 注释 CRUD。
4. 指定版本重建 chunk 和索引。

完成本切片后，在 `FRONTEND_BACKEND_CONTRACT.md` 的数据设计章节记录最终字段和生命周期。

## 7. 数据设计

### 7.1 `poem_sources`

记录作品的来源证据和原始字段，允许同一作品存在多个来源。

关键字段：

```text
id
poem_id
source_type
source_key
source_name
external_id
source_url
raw_title
raw_author_name
raw_dynasty_name
raw_content
raw_payload
content_hash
license_note
fetched_at
created_at
updated_at
```

约束：

1. `UNIQUE(source_key, external_id)`。
2. `INDEX(content_hash)`。
3. 删除作品时来源记录级联删除。

### 7.2 `poem_versions`

不可变版本快照。`version_no` 与 `poems.version_no` 在生成时保持一致。

关键字段：

```text
id
poem_id
source_id
version_no
snapshot
content_hash
change_type
changed_by_id
change_note
created_at
```

约束：

1. `UNIQUE(poem_id, version_no)`。
2. `source_id`、`changed_by_id` 删除时设为 `NULL`。
3. `snapshot` 至少包含标题、作者、朝代、正文、规范化正文、摘要、状态、分类和标签。
4. 只记录创建、内容编辑、归档、恢复和导入；发布和撤回不改变版本号，仅改变索引可见状态。

### 7.3 `poem_annotations`

保存注释、译文、赏析、背景和典故，关联到作品版本。

关键字段：

```text
id
poem_version_id
source_id
annotation_type
title
content
normalized_content
line_start
line_end
status
content_hash
created_at
updated_at
```

设计规则：

1. `annotation_type` 使用 `note`、`translation`、`appreciation`、`background`、`allusion`、`other`。
2. `line_start` 和 `line_end` 为空表示适用于整首作品。
3. 行号从 1 开始，服务层校验行范围和作品行数。
4. 状态使用 `draft`、`published`、`archived`。

### 7.4 `poem_chunks`

保存可索引文本单元，可追溯到版本、注释和原文行范围。

关键字段：

```text
id
poem_id
poem_version_id
annotation_id
granularity
chunk_index
text
normalized_text
content_hash
line_start
line_end
token_count
vector_id
embedding_model
embedding_dimension
chunk_strategy
status
created_at
updated_at
```

约束：

1. `UNIQUE(poem_version_id, granularity, chunk_strategy, chunk_index)`。
2. `granularity` 第一阶段使用 `poem`、`line`、`note`。
3. `status` 使用 `pending`、`ready`、`failed`、`disabled`。
4. `vector_id` 在有向量后唯一；未向量化时为空。
5. 删除版本时其 chunks 和 annotations 级联删除。

## 8. 后端设计

1. 新增独立 ORM 文件：`source.py`、`version.py`、`annotation.py`、`chunk.py`。
2. 在 `Poem` 上增加 `sources` 和 `versions` 关系。
3. 在 `PoemVersion` 上增加 annotations 和 chunks 关系。
4. `CatalogService` 在创建、编辑、归档和恢复时写版本快照。
5. 当前管理员操作传入 `changed_by_id`，保留操作人追溯能力。
6. 版本快照由稳定 JSON 结构组成，不依赖 ORM 对象序列化。

## 9. 前端设计

不涉及。来源、版本、注释和 chunk 的管理页面在后续 API 完成后设计。

## 10. RAG 与评估

本切片只定义 RAG 输入边界：

1. 检索读取已发布作品对应版本的 chunks。
2. poem chunk 用于整首作品级上下文和相似作品。
3. line chunk 用于出处、句意和相似诗句。
4. note chunk 用于赏析、典故和背景补充。
5. 回答引用至少能回溯到 `chunk -> version -> poem/source/annotation`。

暂不定义召回率等指标，因为没有实际检索和评估集。

## 11. 测试计划

| 层级 | 覆盖内容 |
| --- | --- |
| 模型集成测试 | 来源、版本、注释和 chunk 的持久化及关系 |
| 约束测试 | 同一作品版本号唯一 |
| 现有 API 测试 | 诗词创建、编辑、归档、恢复生成版本快照 |
| 迁移测试 | 升级到新 revision、回滚、已有作品版本回填 |
| 回归测试 | 用户、认证、目录和诗词 CRUD 全量通过 |

## 12. 风险与回滚

1. 当前已有作品没有历史版本，只能在迁移时回填当前快照，不能伪造历史。
2. 版本表会让每次编辑多一次写入，这是可接受的正确性成本。
3. 本迁移只新增表，不改现有列；应用回滚时可保留新增表，数据库回滚时删除新表。
4. 如果版本快照结构变化，必须增加新版本或迁移，不能改写历史快照。

## 13. 实施任务

- [x] 功能设计与范围确认
- [x] 更新前后端数据契约
- [x] 新增 ORM 模型
- [x] 新增 Alembic 迁移和已有版本回填
- [x] 接入 CatalogService 版本快照
- [x] 增加模型和 CRUD 回归测试
- [x] 在真实 MySQL 执行迁移
- [x] 更新项目说明和开发日志

## 14. 决策与变更记录

| 日期 | 决策或变化 | 原因 |
| --- | --- | --- |
| 2026-09-19 | 来源记录与爬取配置解耦 | 人工、文件和未来爬虫共用来源表，避免阻塞在爬虫设计 |
| 2026-09-19 | 版本快照不可变 | 保证 chunk、引用和评估输入稳定 |
| 2026-09-19 | 暂不实现会话和评估表 | 先稳定语料输入，避免一次迁移覆盖整个 RAG |
