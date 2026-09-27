# 领域标注 Schema 设计与治理边界

> 状态：迁移、受控标签种子、管理 API、管理页、诗词编辑弹窗治理、离线语料审计、首批人工复查和标签金标准评估已实现；在线检索尚未接入
> 创建日期：2026-09-27
> 最近更新：2026-09-27
> 关联任务：为意象、情感、题材和典故建立可追溯、可审核的版本级标签

## 1. 背景与问题

当前 `poem_annotations` 适合保存注释、译文、赏析、背景等长文本，但它不是结构化
标签集合；通用 `tags` 和 `categories` 适合基础分类，却缺少领域检索需要的维度、
置信度、生成方式、证据文本和审核状态。

如果直接把 LLM 生成的“月亮、思乡、羁旅”写入通用标签，会产生四个问题：

1. 无法区分人工标签、公开数据集标签和 AI 推断标签。
2. 无法证明某个标签来自哪一句诗、哪个模型或哪个任务版本。
3. 未审核标签可能被直接用于在线过滤，造成错误召回。
4. 标签名称变化后缺少合并、废弃和别名映射，查询改写无法稳定工作。

本切片先冻结数据模型、接口边界和治理规则，再按迁移、受控种子、管理 API 的小步
切片实现；在线检索、查询改写和重排仍未接入。

## 2. 目标

- 建立 `imagery / emotion / theme / allusion` 四个受控标签维度。
- 标签支持规范名、归一化名、别名、合并和废弃。
- 标签关联到 `poem_version_id`，避免作品正文变更后沿用过期标注。
- 每条关联保留来源、生成方式、置信度、证据文本和审核状态。
- 人工标签与 AI 标签可以并存，但在读取时按明确优先级合并。
- 为后续筛选检索、查询改写和重排提供稳定数据源。

## 3. 非目标

- 不调用 LLM 自动抽取标签。
- 本切片不新增前端审核页面；后续已由独立管理页和诗词编辑弹窗补齐治理入口。
- 不把标签直接作为问答答案证据。
- 不把长注释、译文或赏析塞入标签表。
- 不用 JSON 字段把多个领域标签和审核信息混在一条记录中。
- 当前只把标签管理 API 作为治理能力，不把标签过滤接入在线检索和重排。

## 4. 与现有模型的分工

| 现有模型 | 保留职责 | 不承担的职责 |
| --- | --- | --- |
| `poem_annotations` | 注释、译文、赏析、背景、典故长文本 | 多维标签、置信度和审核 |
| `tags` | 通用标签、运营性词汇 | 领域维度、别名语义和来源审核 |
| `categories` | 体裁、题材等粗分类 | 带证据的多标签版本关系 |
| 新领域标注表 | 结构化领域标签、别名、来源和审核 | 长文本正文和问答引用 |

“典故”可以同时存在长篇解释和结构化标签：长篇内容继续放在
`poem_annotations.annotation_type=allusion`，标签表只保存可检索的规范典故名称。

## 5. 方案概览

```text
domain_labels
  1 -> N domain_label_aliases
  1 -> N poem_version_domain_labels

poem_versions
  1 -> N poem_version_domain_labels

poem_sources
  1 -> N poem_version_domain_labels
```

读取策略：

```text
approved manual
  > approved public_dataset
  > approved ai
  > pending/rejected/archived 不进入默认在线结果
```

同一版本、同一规范标签即使由多个来源产生，也只在实际使用视图中去重；数据库保留
多条来源记录用于审计，不覆盖历史证据。

## 6. 数据设计

### 6.1 `domain_labels`

保存规范标签和生命周期状态。

| 字段 | 类型建议 | 约束 |
| --- | --- | --- |
| `id` | bigint PK | 自增 |
| `dimension` | varchar(30) | `imagery/emotion/theme/allusion` |
| `canonical_name` | varchar(80) | 展示名，非空 |
| `normalized_name` | varchar(80) | 查询归一化名，非空 |
| `description` | varchar(500) | 可空，供人工理解 |
| `status` | varchar(20) | `active/merged/deprecated` |
| `merged_into_id` | bigint FK nullable | 指向同一维度目标标签 |
| `created_at` | datetime | UTC |
| `updated_at` | datetime | UTC |

约束与索引：

- 唯一键：`(dimension, normalized_name)`。
- 索引：`(dimension, status)`。
- `merged_into_id` 禁止自引用；合并链在服务层限制为一层或显式展开。
- 已被关联引用的标签不能硬删除，只能 `deprecated` 或 `merged`。

### 6.2 `domain_label_aliases`

保存现代口语、异名和抽取结果到规范标签的映射。

| 字段 | 类型建议 | 约束 |
| --- | --- | --- |
| `id` | bigint PK | 自增 |
| `domain_label_id` | bigint FK | `ON DELETE CASCADE` |
| `alias` | varchar(80) | 展示别名 |
| `normalized_alias` | varchar(80) | 查询归一化别名 |
| `source_id` | bigint FK nullable | 可追溯到导入来源 |
| `created_at` | datetime | UTC |

建议约束：

- 唯一键：`(domain_label_id, normalized_alias)`。
- 索引：`normalized_alias`。
- 同一别名可以映射到不同维度的不同标签，例如人物名和意象名；解析服务必须结合
  维度或返回候选并由查询改写节点决策。
- AI 新造别名进入待审核队列，不能直接写入正式别名表。

### 6.3 `poem_version_domain_labels`

保存作品版本级标签关联和完整来源信息。

| 字段 | 类型建议 | 约束 |
| --- | --- | --- |
| `id` | bigint PK | 自增 |
| `poem_version_id` | bigint FK | `ON DELETE CASCADE` |
| `domain_label_id` | bigint FK | `ON DELETE RESTRICT` |
| `source_id` | bigint FK nullable | 来源记录，`ON DELETE SET NULL` |
| `generation_method` | varchar(30) | `manual/public_dataset/ai` |
| `origin_ref` | varchar(120) | 稳定来源键，非空 |
| `confidence` | decimal(5,4) nullable | 0 到 1；人工可不填 |
| `review_status` | varchar(20) | `pending/approved/rejected/archived` |
| `evidence_text` | text nullable | 形成标签的原文片段 |
| `line_start` | int nullable | 证据起始行 |
| `line_end` | int nullable | 证据结束行 |
| `model_name` | varchar(150) nullable | AI 或公开数据集标注模型 |
| `task_version` | varchar(100) nullable | Prompt、词典或任务版本 |
| `created_by_id` | bigint FK nullable | 人工创建者 |
| `reviewed_by_id` | bigint FK nullable | 审核者 |
| `reviewed_at` | datetime nullable | UTC |
| `created_at` | datetime | UTC |
| `updated_at` | datetime | UTC |
| `archived_at` | datetime nullable | UTC |

建议约束：

- 唯一键：`(poem_version_id, domain_label_id, origin_ref)`。
- 索引：`(poem_version_id, review_status)`。
- 索引：`(domain_label_id, review_status, generation_method)`。
- `line_end` 不得小于 `line_start`。
- `confidence` 不得小于 0 或大于 1。
- `generation_method=ai` 时，`model_name` 和 `task_version` 必填。
- 只有 `approved` 记录可进入默认在线检索过滤。

使用非空 `origin_ref` 而不是可空 `source_id` 作为唯一键的一部分，是为了避免 MySQL
唯一索引允许多个 NULL 来源导致重复行。示例：

```text
manual
public_dataset:chinese-gushiwen-1000-v2
ai:deepseek-chat:domain-label-v1
```

## 7. 审核与生效规则

| 当前状态 | 允许动作 | 结果 |
| --- | --- | --- |
| `pending` | approve | 改为 `approved`，记录审核者和时间 |
| `pending` | reject | 改为 `rejected`，保留证据用于审计 |
| `approved` | archive | 改为 `archived`，默认在线查询不可见 |
| `rejected` | reassess | 新建或更新记录后重新进入 `pending` |

读取优先级：

1. 同一规范标签存在人工 `approved` 时，人工证据优先展示。
2. 没有人工记录时使用公开数据集 `approved`。
3. 只有 AI 标签时必须在界面或 API 元数据中明确 AI 生成。
4. 所有 `pending`、`rejected` 和 `archived` 默认不参与在线筛选。

## 8. 版本、删除与合并策略

1. 标签绑定 `poem_version_id`，新的正文版本不自动继承旧标签。
2. 后续管理端可以提供“从上一版本复制”操作，但复制结果必须默认进入 `pending`。
3. 标签合并时保留原标签记录，将其 `merged_into_id` 指向目标标签。
4. 作品版本删除时关联标签通过外键级联删除。
5. 标签被合并或废弃时，历史关联不删除，以便复现旧评估。
6. 公开数据集重新导入必须携带新 `origin_ref`，不能静默覆盖人工审核结果。

## 9. 接口边界与实现状态

公开读取只返回当前 `PoemVersion` 的 `approved` 标签；合并标签解析到 active 目标，
并按 `manual > public_dataset > ai` 去重。`pending/rejected/archived` 不进入公开
结果。

| 方法 | 路径 | 用途 | 状态 |
| --- | --- | --- | --- |
| GET | `/api/v1/poems/{poem_id}/domain-labels` | 查询当前版本的生效标签 | 已实现 |
| GET | `/api/v1/admin/domain-labels` | 分页查询标签与别名 | 已实现 |
| POST | `/api/v1/admin/domain-labels` | 创建受控标签和别名 | 已实现 |
| PATCH | `/api/v1/admin/domain-labels/{label_id}` | 更新名称、别名、废弃或合并标签 | 已实现 |
| GET | `/api/v1/admin/domain-labels/{label_id}/assignments` | 查询某标签的作品版本关联 | 已实现 |
| GET | `/api/v1/admin/poems/{poem_id}/domain-labels` | 查询作品当前版本的全部标签关联 | 已实现 |
| POST | `/api/v1/admin/poems/{poem_id}/domain-labels` | 为当前版本新增待审核标签 | 已实现 |
| POST | `/api/v1/admin/domain-label-assignments/{assignment_id}/review` | 审核、驳回、归档或重新评估 | 已实现 |

管理接口全部使用管理员鉴权。创建或更新标签时，同维度规范名和归一化别名均去重；
合并只允许同维度 active 目标，且不允许多级合并链。公开数据集和 AI 标签必须提供
`origin_ref`；AI 标签还必须提供 `model_name` 和 `task_version`。审核请求可以在
`pending -> approve/reject` 时可选地修正 `evidence_text`、`line_start` 和
`line_end`，修正后的行号范围必须先通过校验，再执行状态流转。

## 10. RAG 接入与标签评估

### 10.1 离线覆盖率与治理审计（已实现）

审计脚本只读取当前已发布且未删除作品的 `PoemVersion`，输出标签覆盖率、审核构成、
标签来源、每作品标签数量分布，以及非当前版本、非法合并、元数据缺失和跨维度别名
等治理风险。它不会写入标签、改变审核状态或影响在线检索。

复现命令：

```powershell
.\.venv\Scripts\python.exe apps\api\scripts\audit_domain_labels.py `
  --json-output data\eval\reports\domain_label_audit_20260927_human_review.json
```

2026-09-27 首批人工样本审核与人工复查后的真实 MySQL 审计结果：

| 指标 | 结果 |
| --- | ---: |
| 已发布作品 / 当前版本 | 1008 / 1008 |
| 有任一标签关联的作品 | 5 |
| 有 `approved` 关联的作品 | 5 |
| 有在线可见标签的作品 | 5 |
| 在线标签覆盖率 | `0.004960` |
| 标签库 | `18 active` |
| 当前关联 | 14 `approved`、1 `archived` |
| 从未在线可见的 active 标签 | 7 |

首批 15 条标签在审核阶段修正了 7 条证据行号，其中《水调歌头·明月几时有》的
`theme:中秋` 还同时修正了证据文本。人工复查时确认《静夜思》的 `theme:羁旅` 只能
被“望月思乡”支持，诗中没有“客居、旅途、他乡、天涯、漂泊、行旅”等直接证据，因此
通过 `DomainLabelService.review_assignment()` 从 `approved` 归档为 `archived`；
没有新增或替换主题标签，“思乡”已由 `emotion:思乡` 覆盖。最终 `approved=14`、
`archived=1`，`theme` 在线关联从 4 条降为 3 条、覆盖作品从 4 首降为 3 首。除
“7 个 active 标签尚无在线可见关联”外，其余治理风险均为 `0`。当前覆盖率仍只有
5/1008 首，不能进入在线标签过滤或重排。

### 10.2 批量导入契约（已实现）

批量导入使用 `DomainLabelImportDataset` JSON，核心边界如下：

- 作品使用 `poem_source_key + external_id` 定位，不依赖本地自增 `poem_id`，便于重复
  导入同一公开数据集。
- 标签名称按维度解析规范名和别名；同一维度命中多个 active 标签时判定歧义并拒绝，
  不做任意选择。
- 标签关联写入作品当前 `PoemVersion`，并记录作品的 `PoemSource`；新版本不会自动
  继承旧版本标签。
- `origin_ref` 使用解析后的规范标签名构造，因此“月亮”和“月”不会重复写入同一语义
  关联。
- 相同 `(poem_version_id, domain_label_id, origin_ref)` 视为 `unchanged`，不会覆盖已有
  的 `approved`、`rejected` 或归档状态。
- AI 数据集必须提供 `model_name` 和 `task_version`；所有新关联固定进入 `pending`。
- 每条记录使用独立 savepoint；单条坏记录不会回滚其他成功记录。
- dry-run 连接真实数据库执行只读预检，返回“将会创建”的数量，但不写入、不提交、
  不改变审核状态。

复现命令：

```powershell
.\.venv\Scripts\python.exe apps\api\scripts\import_domain_labels.py `
  --input data\import\example_domain_labels_v2.json `
  --dry-run
.\.venv\Scripts\python.exe apps\api\scripts\import_domain_labels.py `
  --input data\import\example_domain_labels_v2.json
```

首批样本覆盖 5 首作品、15 条标签关联；首次导入为 `5 created / 15 assignments`，
重复导入为 `5 unchanged / 15 assignments`。人工复查后的 v2 保留 `v1` 批次号以维持
`origin_ref` 幂等，移除已归档标签后 dry-run 为 `5 unchanged / 14 assignments`；
`v1` 只作为历史审查输入，新环境应导入 v2。导入结果保存于
`data/eval/reports/domain_label_import_20260927.json`，导入后审计保存于
`data/eval/reports/domain_label_audit_20260927_after_import.json`，审核后审计保存于
`data/eval/reports/domain_label_audit_20260927_reviewed.json`，人工复查后审计保存于
`data/eval/reports/domain_label_audit_20260927_human_review.json`。逐条复查材料见
[首批 15 条领域标签人工复查单](../reviews/20260927-domain-label-first-batch-review.md)。

### 10.3 在线接入顺序

完成标签金标准与覆盖率建设后，按以下顺序接入：

1. 查询改写把“想家的诗”默认映射为 `emotion:思乡`、`imagery:月`；只有查询明确
   表达客居、漂泊或行旅时，才额外映射 `theme:羁旅`。
2. 结构化过滤先按 approved 标签缩小候选，再执行现有混合检索。
3. 重排可以按标签匹配度加权，但标签不能覆盖正文证据。
4. 生成回答仍必须引用 chunks；标签只辅助检索，不作为事实引用。

### 10.4 评估指标

标签层已增加只读金标准评估 CLI。数据集先校验来源内容哈希、标签解析、证据行号和
作品唯一性，再以 `(external_id, label_id)` 为最小匹配单位计算：

- `micro`：对全部有金标准的维度汇总 TP、FP、FN，再计算全局 precision、recall 和
  F1；别名或合并标签先解析到同一 active 规范标签。
- `macro`：分别计算每个维度的 P/R/F1，再对当前存在金标准标签的维度等权平均。
  没有金标准的维度不进入 macro 分母，避免用空集合压低或抬高总体分数。
- `critical_error_rate`：`critical` 金标准标签漏标数 / `critical` 金标准标签总数；
  分母为 0 时返回 `0.0`。
- `evidence_error_rate`：已命中金标准标签但证据行号未落入任何金标准范围的记录数 /
  micro TP；缺少证据文本或完整行号范围同样计为错误。
- `coverage`：金标准作品数 / 当前已发布作品数、各维度目标覆盖率和目标缺口，用于
  区分“评估器可运行”与“样本量足以支持策略切换”。

复现命令：

```powershell
.\.venv\Scripts\python.exe apps\api\scripts\evaluate_domain_labels.py `
  --input data\eval\domain_label_gold_v1.json `
  --json-output data\eval\reports\domain_label_gold_v1_20260927.json
```

首批 `domain-label-gold-v1` 覆盖《静夜思》《春晓》《登鹳雀楼》《水调歌头·明月几时
有》和《天净沙·秋思》，共 5 首作品、14 条人工确认标签、8 条 `critical` 标签。
当前离线结果：

| 指标 | 结果 |
| --- | ---: |
| micro P/R/F1 | `1.0 / 1.0 / 1.0` |
| macro P/R/F1 | `1.0 / 1.0 / 1.0` |
| TP / FP / FN | `14 / 0 / 0` |
| critical 漏标 / 严重错误率 | `0 / 0.0` |
| 证据错误率 | `0.0` |
| 已发布作品覆盖率 | `0.004960` |
| macro 计划覆盖率 | `0.281250` |
| 计划缺口 | `24` |
| 有金标准的 macro 维度 | `imagery`、`emotion`、`theme` |

这份金标准来自已完成人工审核与复查的样本，因此当前 `1.0` 只证明评估器、在线可见
标签规则和已审核结果一致，属于同源一致性检查；它不能证明标签器或导入标签对未观察
作品的泛化精度。当前按维度计划仍有缺口：imagery `5/12`、emotion `4/12`、
theme `3/8`、allusion `0/4`。allusion 尚无金标准样本，其单维度 P/R/F1 保持
`0.0`，但不进入 macro 平均。建立独立于 AI 与导入产物的人工样本前，标签过滤和重排
继续保持关闭。

- 检索层：标签过滤前后 Recall@5、MRR、nDCG@5 和拒答准确率。
- 系统层：标签查询延迟、候选缩减率、无结果率和审核积压量。

## 11. 测试计划

| 层级 | 覆盖内容 |
| --- | --- |
| Schema | 维度枚举、唯一键、置信度范围和行号范围 |
| Repository | 来源保留、审核状态迁移、标签合并 |
| Service | 人工/数据集/AI 优先级和在线可见性 |
| API | 权限、分页、默认只返回 approved |
| 数据治理 | 重复 `origin_ref` 幂等和版本复制 |
| 评估 | Schema、别名解析、优先级、P/R/F1、严重漏标、证据错误、内容哈希和冻结数据集回归 |

## 12. 风险与回滚

1. 标签体系容易被无限扩张；每日新增标签必须经过审核和合并。
2. AI 标签存在幻觉和过度标注，未审核结果不得进入在线检索。
3. `evidence_text` 与行号可能随版本变化失真；行号只作为提示，正文证据优先。
4. 同一别名跨维度可能产生歧义，查询改写需要显式维度和候选回退。
5. 新增表不修改现有表，迁移回滚相对直接；一旦产生人工审核数据，不能无备份删除。
6. 标签过滤可能减少召回，必须先在旧集回归，再在未观察 v5/v6 上验证。

## 13. 实施任务

- [x] 明确与现有 annotation、tag、category 的边界
- [x] 设计三张规范化表和唯一约束
- [x] 定义来源、置信度、证据和审核状态
- [x] 定义版本继承、合并、废弃和删除策略
- [x] 定义未来 API、RAG 接入和评估指标
- [x] 创建 Alembic 迁移
- [x] 建立初始受控标签和别名种子
- [x] 实现管理端录入与审核
- [x] 接入离线标签覆盖率与治理审计
- [x] 实现领域标签批量导入、dry-run、别名解析和幂等写入
- [x] 完成首批 15 条人工审核与复查，最终形成 `approved=14`、`archived=1`
- [x] 新增复查修订版 v2 重放数据集，移除已归档的错误标签
- [x] 建立人工标签金标准并计算 precision、recall 和 macro F1
- [ ] 再决定是否用于在线过滤和重排

实现记录：

- `apps/api/migrations/versions/20260927_0008_create_domain_labels.py` 创建三张表及索引。
- `apps/api/app/models/domain_label.py` 定义 ORM 模型、状态枚举和关键 CHECK 约束。
- `apps/api/app/db/seed.py` 增加 18 个受控标签和 35 个别名，重复执行不新增重复记录。
- `apps/api/app/repositories/domain_labels.py` 提供分页、当前版本、公开 approved 和
  `origin_ref` 幂等查询。
- `apps/api/app/services/domain_labels.py` 实现标签 CRUD、别名替换、同维度唯一、
  合并、默认待审核关联和审核状态机。
- `apps/api/app/api/v1/admin/domain_labels.py`、`apps/api/app/api/v1/admin/poems.py`
  和 `apps/api/app/api/v1/poems.py` 暴露管理端与公开读取路由。
- `apps/api/tests/test_domain_labels.py` 覆盖权限、CRUD、别名、幂等、审核、公开
  可见性、AI 元数据、非法流转、合并和废弃。
- `apps/web/src/views/AdminDomainLabelView.vue`、`apps/web/src/api/admin.ts` 和
  `apps/web/src/types/api.ts` 增加领域标签管理页、API 方法和类型，支持标签库
  筛选维护、合并/废弃、作品版本关联筛选和审核动作。
- `apps/web/src/components/PoemDomainLabelPanel.vue` 和
  `apps/web/src/views/AdminPoemView.vue` 在诗词编辑弹窗中增加当前版本标签关联
  列表、人工/公开数据集/AI 来源录入和审核动作，新增关联统一进入 `pending`。
- 真实 MySQL 升级到 `20260927_0008`，`alembic check` 返回
  `No new upgrade operations detected.`。
- 完整 `.\scripts\verify.ps1` 通过：后端 `287 passed, 1 skipped, 3 warnings`、
  Ruff、前端 typecheck、Vitest `9 passed` 和生产构建均通过。
- MySQL 不允许带 `ON DELETE SET NULL` 的外键列同时参与 CHECK 约束；因此
  `merged_into_id <> id` 不在数据库层检查，继续按设计由服务层限制。
- `apps/api/app/schemas/domain_label_evaluation.py`、`apps/api/app/evaluation/domain_labels.py`：
  定义离线审计报告结构，并统计当前语料覆盖率、审核积压、来源构成和治理风险。
- `apps/api/scripts/audit_domain_labels.py`：新增只读审计 CLI，支持完整 JSON 报告输出。
- `apps/api/tests/test_domain_label_audit.py`：覆盖当前版本范围、合并标签解析、审核
  构成、非法合并和来源元数据风险。
- `data/eval/reports/domain_label_audit_20260927.json`：保存首个真实语料审计结果。
- `apps/api/app/schemas/domain_label_import.py`：定义导入数据集、标签、记录和报告
  契约，并校验 AI 元数据、重复作品、行号和 `origin_ref` 长度。
- `apps/api/app/services/domain_label_import.py`：按来源定位作品，解析规范标签或别名，
  写入当前版本 `pending` 关联，使用 savepoint 隔离坏记录并保持幂等。
- `apps/api/scripts/import_domain_labels.py`：新增批量导入 CLI，支持 `--dry-run` 和
  完整 JSON 报告输出。
- `apps/api/tests/test_domain_label_import.py`：覆盖 dry-run 只读、规范标签与别名
  解析、来源与版本绑定、幂等且不覆盖审核、失败隔离、歧义拒绝和 AI 元数据。
- `data/import/example_domain_labels_v1.json`：5 首人工审核样本，共 15 条标签。
- `data/eval/reports/domain_label_import_20260927.json` 和
  `data/eval/reports/domain_label_audit_20260927_after_import.json`：保存真实导入与
  导入后审计结果。
- `apps/api/app/schemas/domain_label.py`：审核请求新增可选 `evidence_text`、
  `line_start` 和 `line_end`，并校验行号范围。
- `apps/api/app/services/domain_labels.py`：在 `pending -> approve/reject` 状态流转前
  应用审核修正，并再次校验修正后的有效行号范围。
- `apps/api/tests/test_domain_labels.py`：覆盖审核时修正证据、公开读取修正结果和
  非法行号范围返回 `422`。
- `data/import/example_domain_labels_v1.json`：保留审核阶段修正的 7 条行号和 1 条
  证据文本，作为人工复查的历史输入。
- `data/import/example_domain_labels_v2.json`：移除复查归档的《静夜思》
  `theme:羁旅`，沿用 v1 批次号以保持 `origin_ref` 幂等，供新环境重放。
- `apps/api/tests/test_domain_label_import.py`：同时校验 v1 历史数据集和 v2 重放
  数据集，确认 v2 共 14 条标签且《静夜思》只保留月与思乡。
- `data/eval/reports/domain_label_audit_20260927_reviewed.json`：保存首批审核后的
  真实审计结果。
- `data/eval/reports/domain_label_audit_20260927_human_review.json`：保存人工复查
  归档后的真实审计结果。
- `apps/api/app/schemas/domain_label_gold.py`：定义冻结金标准、来源、证据、标签、
  维度覆盖率、质量摘要和逐作品评估报告契约。
- `apps/api/app/evaluation/domain_label_gold.py`：实现只读金标准评估、别名与合并
  解析、人工/公开数据集/AI 优先级去重、micro/macro 指标、严重漏标和证据校验。
- `apps/api/scripts/evaluate_domain_labels.py`：新增只读评估 CLI，支持控制台摘要和
  完整 JSON 报告输出，不写数据库、不改审核状态、不重建索引。
- `apps/api/tests/test_domain_label_gold.py`：覆盖 Schema、重复标签、空集合、micro
  macro、critical 漏标、额外标签、证据行号、内容哈希、冻结数据集和语料哈希一致。
- `data/eval/domain_label_gold_v1.json`：冻结 5 首作品、14 条人工确认标签、8 条
  `critical` 标签及各维度目标数量。
- `data/eval/reports/domain_label_gold_v1_20260927.json`：保存首次真实评估结果和
  逐作品缺口。

## 14. 决策与变更记录

| 日期 | 决策或变化 | 原因 |
| --- | --- | --- |
| 2026-09-27 | 不直接复用通用 `tags` | 缺少来源、置信度、证据和审核语义 |
| 2026-09-27 | 标签绑定 `poem_version_id` | 正文版本变化后标签必须重新确认 |
| 2026-09-27 | 人工、公开数据集和 AI 记录并存 | 保留来源差异，读取时再按优先级合并 |
| 2026-09-27 | AI 标签默认不可在线使用 | 先审核，避免幻觉污染检索 |
| 2026-09-27 | 先冻结契约，再按迁移、种子和管理 API 小切片实现 | 每步都可回滚和验证，避免一次性接入在线检索 |
| 2026-09-27 | 标签库治理与作品版本关联治理分两个入口 | 标签名称/合并/废弃属于全局词典，关联创建与审核更贴近具体诗词版本 |
| 2026-09-27 | 诗词编辑弹窗只读写当前 `PoemVersion` 的关联 | 避免旧版本标签被误用于新正文，版本变化后必须重新确认 |
| 2026-09-27 | 批量导入只创建 `pending` 关联，不提供自动批准 | 公开数据集和 AI 标签都可能出错，审核状态不能被导入流程绕过 |
| 2026-09-27 | 使用来源键和外部 ID 定位作品 | 本地自增 ID 不可跨环境复用，公开数据集需要可重复导入 |
| 2026-09-27 | 审核时可以修正证据文本和行号 | 导入数据的行结构可能与被审核版本快照不同，必须先修正证据再批准，避免公开错误行号 |
| 2026-09-27 | 人工复查发现证据不足时通过 `approved -> archived` 退审，并单独维护 v2 重放数据集 | 状态机保留历史证据，同时避免新环境再次导入已确认错误的标签 |
| 2026-09-27 | 金标准评估以 `(external_id, label_id)` 匹配，并只让有金标准的维度进入 macro | 避免同一作品重复来源抬高分值，也避免空维度稀释当前指标 |
| 2026-09-27 | 首批金标准只用于验证评估链路，不用 `1.0` 证明泛化质量 | 样本与审核结果同源，且 allusion 尚无样本；独立样本建立前不能开启在线过滤 |
