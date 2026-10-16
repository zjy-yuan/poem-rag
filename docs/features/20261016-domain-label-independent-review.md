# 领域标签 v2 独立复核工作区

> 状态：已实现（离线复核工作区，41 条标签与 6 条空标签结论等待人工决定）
> 创建日期：2026-10-16
> 最近更新：2026-10-16
> 关联任务：在冻结 `domain-label-gold-v2` 前，对 24 首独立人工盲标结果建立可校验的独立复核流程

## 1. 背景与问题

`domain-label-gold-v2-annotation` 草稿由单标注者在冻结正文上完成，24 首全部
`reviewed`，共 41 条标签和 6 首空标签结论。它的状态是 `ready_for_review`，只表示
填写完成，不等于金标准：

- 单标注者结果没有第二方对标签边界、严重级别和证据行号的确认。
- 标签可能漏标、过度标注，或引用了一条行号正确但不支持该标签的正文。
- 6 首“正文没有受控标签”的结论同样可能是漏标，而不是真的无标签。
- 如果直接冻结为 `domain-label-gold-v2`，后续标签过滤、查询改写和重排会建立在
  未复核的标注上，评估结论无法自证。

本切片把复核结果从标注草稿中独立出来，形成第三份文件：冻结采样清单保持只读，
人工盲标草稿保持不可变原标签，复核文件只允许在明确决定下修正标签。这样既保留原始
盲标证据，又能证明“哪些标签被确认、哪些被修正、哪些被拒绝”。

## 2. 目标

- 从 `domain-label-gold-v2-annotation` 确定性生成 41 条标签复核和 6 条空标签复核。
- 把复核结果绑定标注草稿和采样清单的版本与文件字节级 SHA-256。
- 校验复核必须精确覆盖全部人工标签和全部空标签结论，不能增删作品或条目。
- 保持 `original_label` 不可变，只允许 `needs_change` 决定提交修正标签。
- 对 `approve` 和修正标签执行证据预审：行号必须落在冻结正文内，证据文本必须能在
  对应行中匹配。
- 只有全部复核完成、没有 `pending / uncertain`、空标签结论没有 `reject`、证据预审
  全部通过，才允许进入 `ready_to_freeze`。
- 导出只包含正文、原标签、原证据和空白复核位的 Markdown 复核单。

## 3. 非目标

- 不自动生成、补全或推断任何标签。
- 不读取、展示或比较 AI 预测、公开数据集标签和在线已审核标签。
- 不调用模型，不连接 MySQL，不写数据库，也不改变审核状态。
- 不冻结 `domain-label-gold-v2`，不定义 v2 的 `critical` 规则和 `target_record_counts`。
- 不接入在线标签过滤、查询改写或重排。
- 不新增 HTTP API、前端复核页面或多人协作机制。

## 4. 用户场景与验收标准

| 场景 | 操作 | 预期结果 |
| --- | --- | --- |
| 初始化复核工作区 | 运行 `--init` | 生成 41 条标签复核、6 条空标签复核和 Markdown 复核单 |
| 避免覆盖人工复核 | 对已存在输出重复运行 `--init` | 命令拒绝覆盖并返回错误 |
| 复核过程中校验 | 运行 `--validate` | 输出已决定数量、各决定计数和冻结就绪状态 |
| 核对样本绑定 | 修改标注草稿或采样清单后校验 | 任一 SHA-256 不一致时拒绝 |
| 防止增删条目 | 删除或追加标签/空标签复核 | 覆盖校验失败并列出 missing/extra |
| 保护原标签 | 直接改写 `original_label` | 校验失败并指出对应 `review_id` |
| 允许受控修正 | 对 `needs_change` 填写 `corrected_label` | 校验修正标签证据后接受 |
| 补全空标签结论 | 对空标签结论填写 `needs_change` 和 `corrected_labels` | 校验修正标签证据后接受 |
| 阻塞未决与不确定 | 保留 `pending` 或填 `uncertain` | `ready_to_freeze=false` 并列出阻塞 ID |
| 拒绝无标签金标准 | 拒绝全部标签 | 校验失败，最终金标准至少保留一条标签 |

## 5. 方案概览

```text
domain_label_gold_v2_sampling.json   （只读）
  -> 计算文件 SHA-256
domain_label_gold_v2_annotation.json （只读原标签）
  -> 校验 ready_for_review、覆盖、受控标签、证据行号
  -> create_review()
       -> 每个原标签一条 label review，original_label 不可变
       -> 每个空标签作品一条 empty-record review
  -> 输出 domain_label_gold_v2_review.json（默认 status=pending）
  -> 从同一清单导出人工 Markdown 复核单

人工逐条填写
  -> decision=approve / needs_change / reject / uncertain
  -> needs_change 必须给出 corrected_label 或 corrected_labels
  -> 全部决定完成后把 review.status 改为 ready_to_freeze
  -> validate() 执行绑定、覆盖、不可变、证据预审和冻结边界校验
```

复核决定语义：

| 决定 | 标签复核 | 空标签复核 |
| --- | --- | --- |
| `approve` | 原标签成立，保持不变 | 原结论成立，正文确无受控标签 |
| `needs_change` | 原方向成立，但修正名称、严重级别或证据 | 原结论错误，必须补充正确标签 |
| `reject` | 该标签不应进入金标准 | 会阻塞冻结，应改用 `needs_change` 补标签 |
| `uncertain` | 证据不足确认，阻塞冻结 | 证据不足确认，阻塞冻结 |

`review_id` 由 `external_id` 和归一化标签名确定性生成：
`label:{external_id}:{dimension}:{normalized_name}` 与 `empty:{external_id}`。

## 6. 接口与契约

本切片不新增 HTTP API，使用离线 CLI：

```powershell
.\.venv\Scripts\python.exe apps\api\scripts\manage_domain_label_review.py --init
.\.venv\Scripts\python.exe apps\api\scripts\manage_domain_label_review.py --validate
```

默认路径：

| 参数 | 默认值 |
| --- | --- |
| `--manifest` | `data/eval/domain_label_gold_v2_sampling.json` |
| `--annotation` | `data/eval/domain_label_gold_v2_annotation.json` |
| `--review` | `data/eval/domain_label_gold_v2_review.json` |
| `--review-output` | `docs/reviews/20261016-domain-label-v2-independent-review.md` |

`--init` 和 `--validate` 互斥。`--init` 只写新文件，任一输出已存在时拒绝覆盖；
CLI 出错时返回退出码 `2`，不会留下半完成的自动覆盖结果。命令不调用模型、不连接
MySQL、不改变在线标签可见性。

当前绑定：

- 标注草稿版本 `domain-label-gold-v2-annotation`，SHA-256
  `17283005f45a895eac606ca284b2a6e7eec4a8e3379e6008c896dd035337bcbc`。
- 冻结采样清单 SHA-256
  `6888e0c79748dff90a14005592845f9ae501992cef7d93b38d8e8ef84838fe15`。

## 7. 数据设计

不新增表、不新增迁移。复核文件保留：

- `version`：固定为 `domain-label-gold-v2-independent-review`。
- `source_annotation_version`、`source_annotation_sha256`：证明复核对应的标注草稿。
- `source_manifest_version`、`source_manifest_sha256`：证明复核对应的冻结采样清单。
- `status`：`pending / in_progress / ready_to_freeze`。
- `label_reviews`：每条原标签的 `review_id`、`external_id`、`original_label`、
  `decision`、`corrected_label`、`review_note`。
- `empty_record_reviews`：每个空标签结论的 `review_id`、`external_id`、
  `decision`、`corrected_labels`、`review_note`。

`original_label` 由 Schema 和校验共同保护：`needs_change` 必须提供修正标签，
其他决定不允许携带修正标签；`review_id` 必须唯一，空标签 `external_id` 必须唯一。

## 8. 后端设计

- `apps/api/app/schemas/domain_label_review.py`：定义复核决定、状态、标签复核、
  空标签复核、复核数据集和校验摘要，并约束决定与修正标签的一致性。
- `apps/api/app/evaluation/domain_label_review.py`：实现哈希绑定、确定性生成、精确
  覆盖、不可变原标签、证据预审、冻结边界、Markdown 导出和摘要格式化。
- `apps/api/scripts/manage_domain_label_review.py`：提供 `--init` 和 `--validate`
  离线入口，拒绝覆盖已有复核文件。
- `apps/api/tests/test_domain_label_review.py`：覆盖初始化、哈希不一致、覆盖缺失/
  额外项、不可变原标签、证据错误、未决状态、`needs_change`、空标签结论、冻结条件
  和最终零标签拒绝。

实现不读取标签表、不读取检索结果、不调用模型，也不访问数据库连接。

## 9. 前端设计

不涉及。复核面向单个复核者和本地文件；如果后续需要多人复核、冲突处理或权限隔离，
再单独设计持久化模型和管理页面。

## 10. RAG 与评估

### 10.1 与 v2 冻结的关系

复核工作区只负责产生可信的复核决定，不直接参与在线 RAG。只有 `--validate` 返回
`ready_to_freeze=true` 后，下一步才能：

1. 按复核决定合并最终标签，把 `approve` 与 `needs_change` 的修正结果写入正式
   `domain-label-gold-v2`。
2. 冻结 v2 版本号、正文哈希、证据、`critical` 规则和 `target_record_counts`。
3. 在独立样本上重新计算 micro/macro P/R/F1、critical 漏标率和证据错误率。
4. 评估标签质量达标后，再决定是否进行在线过滤或重排实验。

### 10.2 当前状态

`--validate` 输出：

```text
version=domain-label-gold-v2-independent-review status=pending
labels=41 decided_labels=0 empty_records=6 decided_empty_records=0
pending=47 ready_to_freeze=false
```

47 条复核决定全部为 `pending`，均为阻塞项。复核单本身是空白待人工填写的产物，
不代表任何标签已经被确认。PowerShell 控制台打印中文 `review_id` 时可能受代码页
影响而显示乱码，JSON 和 Markdown 均为正常 UTF-8。

### 10.3 与背景知识分层的关系

复核仍只依据冻结正文，不引入作者生平、编年、创作背景、模型预测或在线审核结果。
依赖背景知识才能得出的标签、问答和赏析，后续单独建立知识增强评估层，与正文盲标
分开保存来源和引用，避免污染当前数据集。

### 10.4 冻结边界

`ready_to_freeze` 需要同时满足：

- `status=ready_to_freeze`。
- 41 条标签复核和 6 条空标签复核全部给出决定，没有 `pending / uncertain`。
- 空标签结论没有 `reject`。
- 所有 `approve` 和修正标签通过证据预审。
- 最终金标准至少保留一条标签。

在线标签过滤、查询改写和重排在 v2 冻结并达标前保持关闭。

## 11. 测试计划

| 层级 | 覆盖内容 |
| --- | --- |
| Schema | 复核决定、状态、修正标签一致性和 `review_id` 唯一性 |
| 服务 | 确定性生成、哈希绑定、精确覆盖、不可变原标签和证据预审 |
| CLI | `--init` 生成 JSON 和 Markdown，重复初始化拒绝覆盖，`--validate` 可校验进行中复核 |
| 冻结边界 | 未决/不确定/空标签 `reject` 阻塞，`needs_change` 可通过，最终零标签拒绝 |
| 盲标约束 | Markdown 不包含预测、标签 JSON 字段或模型输出提示 |

## 12. 风险与回滚

1. 复核只降低单标注者偏差，不能证明标签体系本身完备；覆盖率仍受 24 首样本规模限制。
2. `ready_to_freeze` 只证明复核流程完成，不自动保证 `critical` 规则和维度目标合理。
3. 复核必须逐条给出决定；批量 `approve` 会弱化复核价值，应在复核单上保留依据。
4. 标注草稿或采样清单变化后必须重新初始化复核，不复用旧哈希复核文件。
5. 回滚只需删除新增 CLI、Schema、服务、测试、复核 JSON 和复核单，不涉及数据库或
   在线服务。

## 13. 实施任务

- [x] 定义复核决定、状态和修正标签 Schema
- [x] 实现哈希绑定、精确覆盖、不可变原标签和证据预审
- [x] 实现确定性初始化和 Markdown 复核单导出
- [x] 生成空白复核 JSON 和复核单
- [x] 增加服务、Schema 和冻结边界测试
- [x] 在功能文档、README、项目指南和开发日志中记录边界
- [ ] 人工填写 41 条标签复核和 6 条空标签结论
- [ ] 生成正式 `domain-label-gold-v2` 并定义 `critical` 规则与维度目标
- [ ] 在独立样本上重新计算标签指标并决定是否接入在线过滤或重排

## 14. 决策与变更记录

| 日期 | 决策或变化 | 原因 |
| --- | --- | --- |
| 2026-10-16 | 复核结果独立于标注草稿保存 | 保留原始盲标证据，同时记录修正过程 |
| 2026-10-16 | 复核绑定标注草稿和采样清单双哈希 | 任一上游文件变化都必须使旧复核失效 |
| 2026-10-16 | `original_label` 不可变，修正只走 `corrected_label` | 防止复核过程静默改写原标注 |
| 2026-10-16 | 空标签结论用 `reject` 会阻塞冻结 | 空标签被判定为错误时必须补充正确标签 |
| 2026-10-16 | `approve` 也执行证据预审 | 行号或证据文本异常时不能仅凭人工确认进入金标准 |
| 2026-10-16 | 复核完成后仍需保留至少一条标签 | 避免空标签金标准让指标失去判别力 |
| 2026-10-16 | 复核未完成前不冻结 v2、不开启在线标签策略 | 未复核标注不能作为泛化评估的依据 |
