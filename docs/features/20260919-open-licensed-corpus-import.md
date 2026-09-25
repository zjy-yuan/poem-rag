# 开放许可结构化语料导入

> 状态：已实现  
> 创建日期：2026-09-19  
> 最近更新：2026-09-20
> 关联任务：在不依赖爬虫和 HTTP 任务接口的前提下，建立可追溯、幂等、逐条容错的诗词语料导入链路

## 1. 背景与问题

现有诗词目录已经支持管理员 CRUD，RAG 语料表也已具备来源、不可变版本、注释和 chunk 结构，但批量扩充语料仍缺少稳定入口。如果后续爬虫直接写诗词主表，会带来以下问题：

1. 网站字段、页面结构和分类方式会污染核心业务模型。
2. 重复抓取可能产生重复作品、重复版本或重复注释。
3. 来源、许可、原始载荷和抓取时间难以追溯。
4. 单条坏数据可能导致整批导入失败。
5. 爬虫、手工 CRUD 和文件导入可能形成多套不一致的业务逻辑。

因此先定义与爬虫解耦的结构化 JSON 契约和导入 Service。后续爬虫只负责获取与初步解析，并产出同一格式，去重、版本和发布仍由同一套导入逻辑处理。

## 2. 目标

1. 支持从版本化 JSON 文件导入诗词、作者、朝代、分类、标签和注释。
2. 使用 `source_key + external_id` 保证同一来源记录可重复导入而不重复创建。
3. 内容或元数据变化时创建新的不可变版本，不覆盖历史快照。
4. 保留来源 URL、许可说明、原始字段、原始载荷、内容哈希和导入时间。
5. 单条记录失败时隔离事务，后续记录继续处理并写入报告。
6. 默认导入为草稿；只有明确要求发布时才改变发布状态。
7. 提供 `--dry-run`、完整 JSON 报告和可选的 `structural-v1` chunk 重建。
8. 复用 `CatalogService` 的目录、来源和版本能力，不建立第二套写入逻辑。

## 3. 非目标

1. 本切片不实现网站爬虫、页面解析器和站点适配器。
2. 不实现上传文件、创建导入任务或查询任务状态的 HTTP API。
3. 不引入 Celery、后台 Worker、任务租约或自动重试。
4. 不自动生成赏析、译文或注释，也不调用 LLM。
5. 不实现跨来源自动合并、同名作者消歧或版本差异审核界面。
6. 不新增数据库迁移；首版复用现有 `poem_sources` 唯一约束和语料模型。

## 4. 用户场景与验收标准

| 场景 | 操作 | 预期结果 |
| --- | --- | --- |
| 首次导入 | 导入一条带来源的诗词 | 创建草稿诗词、来源记录、版本快照和注释 |
| 重复导入 | 原样再次导入相同数据集 | 返回 `unchanged`，不新增作品、版本或注释 |
| 内容更新 | 修改正文后再次导入 | 版本号递增，创建新版本和新注释，旧版本保留 |
| 发布控制 | 首次以 `publish=true` 导入后以 `false` 重导 | 保持已发布状态，不因 `false` 自动撤回 |
| 坏记录隔离 | 一批中一条记录触发异常 | 该条返回 `failed`，其他合法记录继续导入 |
| 格式预检 | 使用 `--dry-run` | 完成 JSON 和 Schema 校验，不连接数据库、不写数据 |
| 可追溯性 | 查看来源记录 | 能读取来源键、外部 ID、URL、许可、原始载荷、哈希和时间 |

## 5. 方案概览

```text
JSON 数据集
  -> CorpusImportDataset Pydantic 校验
  -> CorpusImportService
  -> source_key + external_id 查询来源
       -> 不存在：创建作品、来源、版本和注释
       -> 已存在：比较规范化字段
            -> 无变化：unchanged
            -> 有变化：更新当前作品并创建新版本
  -> 每条记录 savepoint + commit
  -> CorpusImportReport
  -> 可选 ChunkCatalogService 重建 structural-v1 chunks
```

核心取舍：

1. 使用文件而不是直接写数据库，使爬虫输出、测试夹具和人工整理数据共用契约。
2. 使用现有来源唯一约束作为幂等键，不新增导入批次表。
3. 每条记录独立提交，优先保证坏数据隔离；整批事务回滚留到后续任务化导入再评估。
4. 更新时只追加版本，不修改历史版本和其注释。
5. `publish=false` 表示“不主动发布”，而不是“撤回已有发布”。

## 6. 接口与契约

本切片不新增 HTTP API。

命令行入口：

```powershell
.\.venv\Scripts\python.exe apps\api\scripts\import_corpus.py `
  --input data\import\example_corpus_v1.json `
  --dry-run
```

正式导入并生成报告：

```powershell
.\.venv\Scripts\python.exe apps\api\scripts\import_corpus.py `
  --input data\import\example_corpus_v1.json `
  --report data\import\reports\example-corpus-v1.json `
  --rebuild-chunks
```

参数：

| 参数 | 必填 | 说明 |
| --- | --- | --- |
| `--input` | 是 | UTF-8 JSON 数据集路径 |
| `--report` | 否 | 写入完整 JSON 导入报告 |
| `--dry-run` | 否 | 只校验，不连接数据库、不写数据 |
| `--rebuild-chunks` | 否 | 为本次 `created/updated` 版本重建 `structural-v1` chunks |

顶层结构：

```json
{
  "version": "dataset-v1",
  "source": {
    "source_key": "open-source-key",
    "source_type": "file",
    "source_name": "来源名称",
    "source_url": "https://example.com",
    "license_note": "许可说明"
  },
  "defaults": {
    "publish": false
  },
  "records": []
}
```

记录字段：

| 字段 | 说明 |
| --- | --- |
| `external_id` | 来源内稳定唯一标识；同一数据集内不可重复 |
| `title`、`content` | 必填标题和正文 |
| `author_name`、`dynasty_name` | 可选；不存在时自动创建 |
| `summary` | 可选摘要，不替代正式赏析 |
| `source_url` | 可覆盖顶层来源 URL |
| `publish` | 可覆盖数据集默认发布策略 |
| `categories` | 分类名称和 `CategoryType`，单条最多 30 个 |
| `tags` | 标签，单项 `1..80` 字符，单条最多 30 个，按 NFKC + casefold 去重 |
| `annotations` | 注释、译文、赏析、背景或典故，单条最多 100 个 |
| `raw_payload` | 保留来源原始结构的 JSON，不参与业务查询 |

报告结构：

```json
{
  "dataset_version": "dataset-v1",
  "source_key": "open-source-key",
  "total_records": 1,
  "created_records": 1,
  "updated_records": 0,
  "unchanged_records": 0,
  "failed_records": 0,
  "records": [
    {
      "external_id": "poem-1",
      "status": "created",
      "poem_id": 1,
      "version_id": 1,
      "version_no": 1,
      "message": null
    }
  ]
}
```

`status` 语义固定为：

1. `created`：首次创建。
2. `updated`：检测到内容或受管元数据变化，并创建新版本。
3. `unchanged`：内容、关系、发布要求和注释均一致。
4. `failed`：该条记录失败；当前报告 `message` 可能包含底层异常字符串。

## 7. 数据设计

本切片不新增表和迁移，继续使用：

1. `poems`：保存当前标题、正文、作者、朝代、摘要、状态和版本号。
2. `poem_sources`：保存 `source_key + external_id`、许可、原始载荷、哈希和时间。
3. `poem_versions`：每次导入变化创建 `change_type=import` 的不可变快照。
4. `poem_annotations`：按版本保存导入注释和内容哈希。
5. `poem_categories`、`poem_tags`：复用现有目录关系。
6. `poem_chunks`：仅在指定 `--rebuild-chunks` 且版本有变化时重建。

关键规则：

1. 来源幂等键是 `poem_sources.source_key + external_id`。
2. 内容变化使用 `poems.version_no += 1`，再记录版本快照。
3. 旧版本和旧版本注释不会因再次导入被删除。
4. 作者、朝代、分类和标签不存在时自动创建。
5. `source_type` 支持 `file`、`import`、`crawl` 和 `other`。
6. 没有单独记录“导入批次”；当前通过数据集版本和来源外部 ID 追踪。

## 8. 后端设计

新增：

1. `app/schemas/corpus_import.py`：数据集、来源、记录、注释、结果和报告 Schema。
2. `app/services/corpus_import.py`：创建、比较、更新、版本和逐条事务编排。
3. `apps/api/scripts/import_corpus.py`：文件加载、Schema 校验、数据库会话和报告输出。
4. `apps/api/tests/test_corpus_import.py`：创建、幂等、更新、失败隔离和输入边界测试。

`CatalogService` 开放以下可复用能力：

1. `create_source()`
2. `replace_categories(..., source=...)`
3. `replace_tags()`
4. `record_version()`

每条记录在 `begin_nested()` savepoint 中执行并独立提交。异常会回滚当前记录事务，写入 `failed` 结果，再继续下一条。

## 9. 前端设计

本切片不新增前端页面。当前导入入口是命令行。后续任务化导入需要设计管理员页面时，应覆盖：

1. 上传或选择数据集。
2. 预检结果和字段错误。
3. 导入进度、成功/更新/未变化/失败统计。
4. 失败记录详情和重试。
5. 来源许可、版本差异和发布确认。

## 10. RAG 与评估

本切片只负责生成可追溯的版本和可选 chunks，不改变检索与生成策略。

导入后仍使用同一评估集比较检索效果。新增真实语料后必须：

1. 复核已有评估集金标准是否仍能在新语料中唯一定位。
2. 新增数据集版本，不修改历史基线语义。
3. 区分“语料规模变化”和“检索策略变化”的指标影响。

2026-09-20 已用 `aopao/chinese-gushiwen` 固定 commit 导入 100 首真实语料，并在
原 31 条检索评估集上重跑。扩库后 `lexical-baseline-v1` 为 28/31，
`expanded-lexical-v1` 为 25/31，`hybrid-rrf-v1 + 0.50` 为 23/31；原种子生成集仍为
12/12。该结果说明旧评估集已不足以代表开放语料，随后已建立
`open-corpus-100-v1` 开放语料评估集并切换默认评估入口。完整记录见
`docs/features/20260920-open-corpus-retrieval-evaluation.md`。

## 11. 测试计划

| 层级 | 覆盖内容 |
| --- | --- |
| 单元测试 | 标签规范化与上限、重复 `external_id`、注释行范围 |
| 集成测试 | 创建链路、幂等、版本更新、历史保留、发布语义、坏记录隔离 |
| 契约测试 | JSON Schema、报告结构和 CLI `--help` |
| E2E | 本切片不涉及 |
| 评估 | 本切片不改变检索策略，导入真实语料后重新执行固定评估 |

验证结果：

1. 导入专项测试：`6 passed`，有 2 条第三方测试客户端弃用警告。
2. 后端完整测试：`44 passed`，有 2 条第三方测试客户端弃用警告。
3. 后端 Ruff：通过。
4. 示例数据集 `--dry-run`：通过，识别 `1` 条记录。
5. 100 首真实语料导入：`100 created`、`0 failed`；新增 2039 个 chunks。

## 12. 风险与回滚

1. 当前报告会保存异常字符串；未来暴露为 HTTP API 前必须做错误分类和脱敏。
2. 作者姓名当前按全局规范化姓名匹配，无法区分同名异人。
3. 已存在作者不会因导入记录自动校正朝代关系。
4. 当前按单条提交，导入中途失败会留下前面已成功的记录；再次导入可幂等续跑。
5. 没有批次审计表，不能直接按一次 CLI 执行统一回滚。
6. `raw_payload` 可能保存来源隐私或未清洗字段，正式导入前必须控制内容。
7. 回滚方式是停止执行 CLI 或删除对应测试来源数据；不要回滚已经真实升级的 Alembic 迁移，因为本切片没有新增迁移。

## 13. 实施任务

- [x] 定义结构化 JSON 契约和 Schema 边界
- [x] 复用来源、目录和版本 Service
- [x] 实现创建、幂等更新和版本追加
- [x] 实现逐条失败隔离和报告
- [x] 实现 CLI、`--dry-run` 和可选 chunk 重建
- [x] 增加专项测试和完整回归
- [x] 提供格式示例并完成 dry-run
- [x] 回写项目说明、接口契约和开发日志
- [x] 选择固定来源并完成 100 首小规模导入
- [x] 导入后复核旧评估集并记录语料版本与退化
- [x] 建立开放语料检索评估 v1，并重新标注金标准
- [ ] 任务化导入 HTTP API、权限、批次审计和脱敏
- [ ] 设计同名作者消歧和来源合并审核

## 14. 决策与变更记录

| 日期 | 决策或变化 | 原因 |
| --- | --- | --- |
| 2026-09-19 | 爬虫不直接写诗词主表，先产出统一 JSON | 隔离站点变化，复用同一导入和版本逻辑 |
| 2026-09-19 | 使用 `source_key + external_id` 作为幂等键 | 复用现有唯一约束，无需新增迁移 |
| 2026-09-19 | 每条记录使用 savepoint 并独立提交 | 防止单条坏数据阻断整批导入 |
| 2026-09-19 | `publish=false` 不撤回已发布作品 | 避免重复导入意外改变线上可见性 |
| 2026-09-19 | 内容变化追加新版本，不覆盖历史 | 保留引用、审计和回溯能力 |
| 2026-09-19 | 首版只提供 CLI，不提前实现 HTTP 任务接口 | 先验证数据契约和导入语义，再设计 Worker |
| 2026-09-20 | 固定 commit 和 SHA256，只导入 100 首分层样本 | 先验证真实语料闭环，不提前承担全量数据治理 |
| 2026-09-20 | 扩库后旧评估退化按原报告保留 | 避免通过改金标准或调阈值掩盖召回问题 |
