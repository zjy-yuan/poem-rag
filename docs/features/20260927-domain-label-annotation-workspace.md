# 领域标签独立人工盲标工作区

> 状态：已实现（空白草稿和审阅稿已生成，待人工填写）
> 创建日期：2026-09-27
> 最近更新：2026-09-27
> 关联任务：为 `domain-label-gold-v2-sampling` 建立独立、可校验的人工标注工作区

## 1. 背景与问题

独立盲标采样已经冻结 24 首候选作品，但采样清单本身只包含空白 `manual_labels`。
如果直接在采样文件上填写，正文或采样规则发生变化后很难证明标注结果仍绑定原样本；
如果把预测或公开标签复制进候选，又会破坏盲标属性。

本切片把“冻结样本”和“人工标注结果”分成两个文件：

- 采样清单保持只读，记录样本来源、正文哈希和行号正文。
- 独立草稿记录人工标签、逐作品完成状态和复核备注，并绑定采样清单版本和文件哈希。

这样既能保留原始盲标证据，又能在填写过程中持续校验覆盖范围、受控标签和证据行号。

## 2. 目标

- 从冻结采样清单确定性生成 24 首空白人工标注草稿。
- 生成只包含正文、来源和空白标注位的人工审阅稿，不展示任何模型预测。
- 将草稿绑定采样清单版本、生成时间和文件字节级 SHA-256。
- 校验草稿必须精确覆盖采样清单中的全部 `external_id`，不能增删作品。
- 校验标签属于受控标签库，证据行号不超出冻结正文范围。
- 支持在标注过程中反复执行 `--validate`，不要求一次填完。

## 3. 非目标

- 不自动生成 imagery、emotion、theme 或 allusion 标签。
- 不读取、展示或比较 AI 预测、公开数据集标签和在线已审核标签。
- 不调用模型，不连接 MySQL，不写数据库，也不改变审核状态。
- 不把当前草稿直接视为正式 `domain-label-gold-v2`。
- 不新增 HTTP API 或前端标注页面。

## 4. 用户场景与验收标准

| 场景 | 操作 | 预期结果 |
| --- | --- | --- |
| 初始化工作区 | 运行 `--init` | 生成 24 首空白 JSON 草稿和 Markdown 审阅稿 |
| 避免覆盖人工结果 | 对已存在输出重复运行 `--init` | 命令拒绝覆盖并返回错误 |
| 填写过程中校验 | 运行 `--validate` | 输出 reviewed、pending、标签数和维度计数 |
| 核对样本绑定 | 修改采样清单后校验旧草稿 | SHA-256 不一致时拒绝 |
| 防止增删作品 | 删除或追加 `external_id` | 覆盖校验失败并列出 missing/extra |
| 控制标签范围 | 填写受控标签库之外的值 | 校验失败并指出作品和非法标签 |
| 保护行号证据 | 填写超出正文行数的证据 | 校验失败并报告实际行数 |
| 开始人工复核 | 打开 Markdown 审阅稿 | 只看到正文、来源和空白标注位 |

## 5. 方案概览

```text
domain_label_gold_v2_sampling.json
  -> 读取采样清单并计算文件 SHA-256
  -> create_draft()
       -> 24 首 record，全部 pending、labels 为空
       -> 绑定清单版本、生成时间和 SHA-256
  -> 输出 domain_label_gold_v2_annotation.json
  -> 从同一清单导出人工审阅稿

人工逐首填写
  -> 受控标签
  -> 证据文本和包含式行号
  -> annotation_status=reviewed
  -> 全部 reviewed 后可将草稿状态改为 ready_for_review
  -> validate() 执行绑定、覆盖、标签和行号校验
```

草稿状态分为 `pending`、`in_progress`、`ready_for_review`。逐作品状态只有
`pending` 和 `reviewed`。当草稿标记为 `ready_for_review` 时，所有作品必须已完成，
且至少存在一条标签；单个作品允许在人工复核后保留空标签列表。

## 6. 接口与契约

本切片不新增 HTTP API，使用离线 CLI：

```powershell
.\.venv\Scripts\python.exe apps\api\scripts\manage_domain_label_annotation.py --init
.\.venv\Scripts\python.exe apps\api\scripts\manage_domain_label_annotation.py --validate
```

默认路径：

| 参数 | 默认值 |
| --- | --- |
| `--manifest` | `data/eval/domain_label_gold_v2_sampling.json` |
| `--annotation` | `data/eval/domain_label_gold_v2_annotation.json` |
| `--review-output` | `docs/reviews/20260927-domain-label-v2-annotation.md` |

`--init` 和 `--validate` 互斥。`--init` 只写新文件，任一输出已存在时拒绝覆盖。
CLI 出错时返回退出码 `2`，不会留下半完成的自动覆盖结果。

## 7. 数据设计

不新增表、不新增迁移。草稿保留：

- `version`：固定为 `domain-label-gold-v2-annotation`。
- `source_manifest_version`、`source_manifest_sha256`、`source_manifest_generated_at`：
  证明标注结果对应的采样版本。
- `status`：草稿级状态。
- `records`：每首作品的 `external_id`、`annotation_status`、`labels` 和
  `review_note`。

标签复用 `DomainLabelGoldLabel`：维度必须属于现有受控枚举，名称必须能够在对应维度
解析到 active 规范标签；每条标签至少一个证据，`line_start`、`line_end` 均为包含式
行号。空行计入正文行数。

## 8. 后端设计

- `apps/api/app/schemas/domain_label_annotation.py`：定义草稿状态、逐作品记录、
  标签去重和 `ready_for_review` 完整性规则。
- `apps/api/app/evaluation/domain_label_annotation.py`：实现哈希绑定、确定性草稿、
  覆盖校验、受控标签校验、正文行号边界、Markdown 导出和摘要格式化。
- `apps/api/scripts/manage_domain_label_annotation.py`：提供 `--init` 和
  `--validate` 离线入口，拒绝覆盖已有草稿。
- `apps/api/tests/test_domain_label_annotation.py`：覆盖确定性、盲标、哈希绑定、
  24 首精确覆盖、状态规则、受控标签、重复标签、证据越界和 Markdown 防泄漏。

实现不读取标签表、不读取检索结果、不调用模型，也不访问数据库连接。

## 9. 前端设计

不涉及。当前工作区面向单个标注者和本地文件复核；如果后续需要多人协作、冲突处理或
权限隔离，再单独设计管理页面和持久化模型。

## 10. RAG 与评估

该工作区只负责产生可信的人工标注记录，不直接参与在线 RAG。人工完成并二次复核后，
下一步才能：

1. 将草稿转换为 `DomainLabelGoldDataset`。
2. 冻结 `domain-label-gold-v2` 版本、正文哈希、证据和 critical 规则。
3. 分开统计 v1 同源一致性与 v2 独立泛化指标。
4. 评估标签质量达标后，再决定是否进行在线过滤或重排实验。

## 11. 测试计划

| 层级 | 覆盖内容 |
| --- | --- |
| Schema | 草稿状态、记录状态、标签唯一性、`ready_for_review` 完整性 |
| 服务 | 确定性草稿、清单哈希绑定、精确覆盖、受控标签和行号边界 |
| CLI | `--init` 生成两个文件，重复初始化拒绝覆盖，`--validate` 可校验进行中草稿 |
| 文件回归 | 当前草稿精确覆盖 24 首并绑定采样清单；初始空白属性由确定性生成测试保护 |
| 盲标约束 | Markdown 不包含预测、标签 JSON 字段或模型输出提示 |

## 12. 风险与回滚

1. 文件哈希只证明草稿对应采样清单，不代表人工判断本身正确。
2. `ready_for_review` 只表示标注者完成填写，仍需独立复核才能形成金标准。
3. 人工标签必须来自正文直接证据；作者生平、作品常识和模型记忆不能替代证据。
4. 正文或采样清单变化后必须重新初始化，不复用旧哈希草稿。
5. 回滚只需删除新增 CLI、Schema、服务、测试、空白草稿和审阅稿，不涉及数据库或
   在线服务。

## 13. 实施任务

- [x] 定义独立草稿和校验 Schema
- [x] 实现哈希绑定、覆盖、受控标签和证据行号校验
- [x] 实现确定性初始化和人工审阅稿导出
- [x] 生成 24 首空白草稿
- [x] 增加服务、Schema 和冻结文件测试
- [x] 在功能文档、README、项目指南和开发日志中记录边界
- [ ] 用户逐首填写人工标签
- [ ] 独立复核并冻结为 `domain-label-gold-v2`

## 14. 决策与变更记录

| 日期 | 决策或变化 | 原因 |
| --- | --- | --- |
| 2026-09-27 | 不直接修改冻结采样清单 | 保留原始盲标证据，避免填写过程改变样本契约 |
| 2026-09-27 | 草稿绑定清单文件字节 SHA-256 | 采样文件变化后必须使旧标注失效 |
| 2026-09-27 | 草稿必须精确覆盖全部 24 首 | 防止只填熟悉作品或遗漏候选 |
| 2026-09-27 | 允许 reviewed 作品无标签 | 没有受控标签本身就是有效的人工结论 |
| 2026-09-27 | 审阅稿不输出预测和标签字段 | 保持人工标注对模型、导入和审核结果盲化 |
