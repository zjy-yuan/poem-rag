# 领域标注 Schema 设计与治理边界

> 状态：迁移、受控标签种子和管理 API 已实现；在线检索尚未接入
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
- 不新增前端审核页面。
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
2. 管理端可以提供“从上一版本复制”操作，但复制结果默认进入 `pending`。
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
| POST | `/api/v1/admin/poems/{poem_id}/domain-labels` | 为当前版本新增待审核标签 | 已实现 |
| POST | `/api/v1/admin/domain-label-assignments/{assignment_id}/review` | 审核、驳回、归档或重新评估 | 已实现 |

管理接口全部使用管理员鉴权。创建或更新标签时，同维度规范名和归一化别名均去重；
合并只允许同维度 active 目标，且不允许多级合并链。公开数据集和 AI 标签必须提供
`origin_ref`；AI 标签还必须提供 `model_name` 和 `task_version`。

## 10. RAG 与评估

未来接入顺序：

1. 查询改写把“想家的诗”映射为 `emotion:思乡`、`imagery:月`、`theme:羁旅`。
2. 结构化过滤先按 approved 标签缩小候选，再执行现有混合检索。
3. 重排可以按标签匹配度加权，但标签不能覆盖正文证据。
4. 生成回答仍必须引用 chunks；标签只辅助检索，不作为事实引用。

评估指标：

- 标签层：按维度统计 precision、recall、macro F1 和人工严重错误率。
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
| 评估 | 人工金标准标签集与检索指标回归 |

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
- [ ] 接入离线标签评估
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
- 真实 MySQL 升级到 `20260927_0008`，`alembic check` 返回
  `No new upgrade operations detected.`。
- MySQL 不允许带 `ON DELETE SET NULL` 的外键列同时参与 CHECK 约束；因此
  `merged_into_id <> id` 不在数据库层检查，继续按设计由服务层限制。

## 14. 决策与变更记录

| 日期 | 决策或变化 | 原因 |
| --- | --- | --- |
| 2026-09-27 | 不直接复用通用 `tags` | 缺少来源、置信度、证据和审核语义 |
| 2026-09-27 | 标签绑定 `poem_version_id` | 正文版本变化后标签必须重新确认 |
| 2026-09-27 | 人工、公开数据集和 AI 记录并存 | 保留来源差异，读取时再按优先级合并 |
| 2026-09-27 | AI 标签默认不可在线使用 | 先审核，避免幻觉污染检索 |
| 2026-09-27 | 先冻结契约，再按迁移、种子和管理 API 小切片实现 | 每步都可回滚和验证，避免一次性接入在线检索 |
