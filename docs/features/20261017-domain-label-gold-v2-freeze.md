# 领域标签金标准 v2 冻结

> 状态：已实现（金标准已冻结，首个独立泛化评估为零覆盖基线）
> 创建日期：2026-10-17
> 最近更新：2026-10-17
> 关联任务：把独立复核后的 24 首人工盲标冻结为 `domain-label-gold-v2`，并跑首个独立泛化评估

## 1. 背景与问题

v2 的独立复核已经完成：41 条标签复核全部 `approve`，6 条空标签结论中 4 条
`approve`、2 条 `needs_change`（分别补充旷达与杨柳/伤春/旷达），最终 45 条标签、
20 首有标签、4 首保持空标签，`status=ready_to_freeze`。但在此之前有两个问题必须
先解决：

- **v1 的 1.0 分是循环论证**：v1 金标准本身由 `import_domain_labels.py` 从在线已
  批准标签导入，再拿同一批标签做评估，micro P/R/F1=1.0 只证明导入回环自洽，不能
  说明标签体系在新样本上的泛化能力。
- **severity 不能继续逐首主观判断**：v1 的 critical/normal 由标注者逐首主观给定，
  同一批标签在不同人手里会得到不同级别，评估的漏标权重因此不稳定。v2 改为规则表
  驱动：severity 只由（维度，标签名）是否命中 critical 规则表决定。

本切片把复核结果冻结为正式金标准，同时把 severity 赋值从“人”换成“规则表”，并首次
在人工盲标样本上独立评估在线标签质量。

## 2. 目标

- 从复核文件确定性合并最终标签（`approve` 保留原标签，`needs_change` 取修正标签）。
- severity 完全由 critical 规则表按（维度，标签名）赋值，覆盖标注里的主观值。
- 用规则表对 critical 标签做证据审计：证据未命中强触发词时如实记录审计问题。
- 冻结正文哈希沿用采样清单，金标准与清单逐首绑定。
- CLI 拒绝覆盖已有产物；两次冻结字节一致。
- 在冻结金标准上跑首个独立泛化评估，如实报告指标。

## 3. 非目标

- 不生成、补全或推断任何标签；标注与复核结论之外一个标签都不新增。
- 不构建 AI 标签生成管线（`generation_method=ai` 的分配在代码中尚不存在）。
- 不读取、展示或修改在线已审核标签，不改变任何审核状态。
- 不调用模型，不连接 MySQL，不写数据库（仅评估命令只读连接）。
- 不接入在线标签过滤、查询改写或重排，仍保持关闭。
- 不做 v3 采样，不改采样清单。

## 4. 用户场景与验收标准

| 场景 | 操作 | 预期结果 |
| --- | --- | --- |
| 冻结金标准 | 运行 freeze CLI | 生成 24 记录、45 标签、18 critical 的 `domain-label-gold-v2` |
| severity 规则化 | 检查冻结产物 | 每条标签 severity 等于规则表赋值；17 条由 normal 提升为 critical |
| critical 审计 | 检查审计报告 | 证据未命中强触发词的 critical 标签如实列出（本轮 1 条羁旅） |
| 防止覆盖 | 对已存在输出重复冻结 | 命令拒绝覆盖并返回退出码 2，原文件不变 |
| 可复现性 | 同一输入冻结两次 | 两次 JSON 字节一致 |
| 冻结前置条件 | 复核未 ready 时冻结 | 拒绝冻结并指出 pending/blocking 数量 |
| 泛化评估 | 评估命令 | 输出 micro/macro P/R/F1 与 critical 漏标率 |

## 5. 方案概览

```text
domain_label_gold_v2_sampling.json   （只读，提供标题/作者/正文哈希）
domain_label_gold_v2_annotation.json （只读，提供盲标备注）
domain_label_gold_v2_review.json     （ready_to_freeze，提供最终标签与复核备注）
  -> freeze()
       -> 校验 ready_to_freeze 与复核覆盖
       -> build_final_label_map 合并 approve / needs_change 标签
       -> 每条标签按 CRITICAL_PATTERNS 规则表重赋 severity
       -> audit_critical_label 用强触发词审计 critical 标签证据
       -> 正文哈希取自采样清单，备注拼接“盲标备注；复核备注”
       -> 输出 domain_label_gold_v2.json（24 记录、45 标签、18 critical）
  -> evaluate_domain_labels.py
       -> 解析金标准记录到已发布诗歌当前版本（校验正文哈希）
       -> 读取该版本 APPROVED 的 PoemVersionDomainLabel 作为预测
       -> 计算 micro/macro P/R/F1、critical 漏标率、证据错误率
```

severity 规则表设计：只放“高精度、低歧义、可正则精确匹配”的白名单。

| 维度 | critical 标签 | 规则要点 |
| --- | --- | --- |
| imagery | 月、杨柳、流水、落花、夕阳、鸿雁 | 月排除“岁月/月余/月份（非十五）”；鸿雁排除雁门等地名；杨柳含“柳斜”等弱变体 |
| theme | 边塞、山水田园、登临、羁旅、中秋 | 只用强触发词组合（玉门/阳关/渔舟/登高+楼台/万里+客等），单字“关/山/望”不触发 |
| emotion | 全部 normal | 伤春、孤独、思乡、旷达、离别边界主观，规则化会被弱证据争议拖死 |
| allusion | 全部 normal | 庄周梦蝶、霸王别姬稀有，漏标不致命 |

规则是审计工具而非冻结门槛：命中规则表的标签才审计，审计问题如实记录，不阻塞冻结。

## 6. 接口与契约

本切片不新增 HTTP API，使用离线 CLI：

```powershell
.\.venv\Scripts\python.exe apps\api\scripts\freeze_domain_label_gold.py
.\.venv\Scripts\python.exe apps\api\scripts\freeze_domain_label_gold.py `
  --audit-output data\eval\reports\domain_label_gold_v2_critical_audit.json
.\.venv\Scripts\python.exe apps\api\scripts\evaluate_domain_labels.py `
  --input data\eval\domain_label_gold_v2.json `
  --json-output data\eval\reports\domain_label_gold_v2_20261017.json
```

默认路径：

| 参数 | 默认值 |
| --- | --- |
| `--manifest` | `data/eval/domain_label_gold_v2_sampling.json` |
| `--annotation` | `data/eval/domain_label_gold_v2_annotation.json` |
| `--review` | `data/eval/domain_label_gold_v2_review.json` |
| `--output` | `data/eval/domain_label_gold_v2.json` |
| `--audit-output` | 可选，写 critical 审计 JSON 报告 |

CLI 出错时返回退出码 `2`；任一输出已存在时拒绝覆盖。冻结命令不调用模型、不连接
MySQL；评估命令只读数据库。

当前绑定：

- 采样清单 SHA-256
  `6888e0c79748dff90a14005592845f9ae501992cef7d93b38d8e8ef84838fe15`。
- 标注草稿 SHA-256
  `17283005f45a895eac606ca284b2a6e7eec4a8e3379e6008c896dd035337bcbc`。
- 复核文件 SHA-256
  `a5a6633768f144b55d15af729eb67ce5ee254c54a696bd5471c72b639496875c`。
- 冻结金标准 SHA-256
  `9c15f59fcb6623f538c8b73bb059fbd3f30d9ea1ca84f39a5fd497874d12901f`。

## 7. 数据设计

不新增表、不新增迁移。冻结金标准 JSON 保留：

- `version`：`domain-label-gold-v2`。
- `source`：项目人工金标准，禁止公开分发。
- `poem_source_key`：`aopao-chinese-gushiwen`，与评估解析一致。
- `target_record_counts`：imagery 12 / emotion 12 / theme 8 / allusion 4，
  沿用 v1 项目级计划以保持跨版本可比。
- `records`：逐首 `external_id`、标题、作者、`expected_source_content_hash`、
  `review_note`（“盲标备注；复核备注”拼接，超 1000 字报错）、`labels`。
- 每条标签：维度、名称、规则表 severity、证据行号；标签名去重由 Schema 保证。

## 8. 后端设计

- `apps/api/app/schemas/domain_label_gold_freeze.py`：定义 critical 审计问题、
  冻结摘要与审计报告 Schema。
- `apps/api/app/evaluation/domain_label_gold_freeze.py`：实现规则表（`include` 强
  触发词 + `exclude` 排除片段）、severity 解析、证据审计、冻结合并与摘要格式化。
- `apps/api/app/evaluation/domain_label_review.py`：`_build_final_label_map` 重构为
  模块级公开函数 `build_final_label_map`，供冻结复用。
- `apps/api/scripts/freeze_domain_label_gold.py`：离线冻结入口，拒绝覆盖。
- `apps/api/tests/test_domain_label_gold_freeze.py`：18 个用例，覆盖规则表赋值、
  排除语义、审计、冻结边界、产物不变量、确定性与 CLI 拒绝覆盖。

冻结不读取标签表、不调用模型、不连接数据库。

## 9. 前端设计

不涉及。金标准是离线评估资产；如后续做多人复核或标注管理界面，再单独设计。

## 10. RAG 与评估

### 10.1 首个独立泛化评估结果（零覆盖基线）

```text
micro p/r/f1 = 0.000000 / 0.000000 / 0.000000
macro p/r/f1 = 0.000000 / 0.000000 / 0.000000
tp=0 fp=0 fn=45 critical_missing=18 critical_rate=1.000000 evidence_errors=0
gold_scope=0.023810 predicted_records=0 macro_plan=0.708333 plan_gaps=6
```

逐维度（target / gold / predicted / plan_coverage / gap）：

- imagery：12 / 10 / 0 / 0.833333 / 2
- emotion：12 / 16 / 0 / 1.000000 / 0
- theme：8 / 8 / 0 / 1.000000 / 0
- allusion：4 / 0 / 0 / 0.000000 / 4

### 10.2 结果解读：这是覆盖基线，不是质量结论

数据库里全部 `PoemVersionDomainLabel` 只有 15 条（14 条 approved manual、1 条
archived manual），全部来自 v1 金标准导入；AI 标签生成管线尚未构建，24 首金标准
诗歌从未产生任何在线标签分配。因此本轮的 0.0 是“在线标签器在独立样本上零覆盖”
的基线，不是“标签器质量差”的结论：没有预测自然没有 TP。

v1 的 1.0 与 v2 的 0.0 是两个方向的教科书案例：v1 用导入的标签评估导入的标签，
是回环自洽；v2 用人工盲标评估在线标签，才第一次反映真实泛化差距。这个对比本身就是
面试里“你怎么证明你的评估不是自说自话”的标准答案。

### 10.3 下一步

1. 构建 AI 标签生成管线：对诗歌当前版本生成 pending 标签分配，走人工审核流。
2. 在 24 首金标准诗歌上生成并审核标签后，用同一评估命令重算指标，得到首个真实
   泛化 P/R/F1 与 critical 漏标率。
3. 指标达标后再设计在线过滤、查询改写或重排实验，当前保持关闭。

## 11. 测试计划

| 层级 | 覆盖内容 |
| --- | --- |
| 规则表 | severity 只由规则表决定；月/杨柳/边塞/中秋为 critical，孤独/庄周梦蝶为 normal |
| 排除语义 | “岁月”不命中月，“雁门”不命中鸿雁，“六月十五”仍命中月，“望明月”不触发登临 |
| 审计 | 证据“桃李春风一杯酒”对羁旅产生审计问题，“万里悲秋常作客”不产生 |
| 冻结边界 | 复核未 ready_to_freeze 时拒绝冻结 |
| 产物不变量 | 24 记录、45 标签、18 critical、维度计数、正文哈希与清单一致、备注拼接规则 |
| severity 覆盖 | 冻结产物每条标签 severity 等于规则表赋值（17 条被提升） |
| 确定性 | 同一输入两次冻结字节一致 |
| CLI | 拒绝覆盖已有输出，退出码 2，原文件不变 |

## 12. 风险与回滚

1. 规则表是起点不是终态：本轮“柳斜”就漏在字符类外，靠审计暴露后补齐；后续使用中
   仍会迭代（只加高精度强触发词，不加单字泛触发词）。
2. 羁旅标签“桃李春风一杯酒，江湖夜雨十年灯”证据不含任何强触发词，审计如实记录，
   不自动改判；这暴露规则表对“全文意象型”证据无能为力，属已知软肋。
3. 零覆盖基线依赖数据库现状；一旦有人导入或生成标签，指标必须重算，不能引用旧报告。
4. 24 首样本规模有限，macro 只覆盖 3 个有标签维度。
5. 回滚只需删除金标准 JSON、审计报告、新增模块、CLI 与测试，不涉及数据库或在线服务。

## 13. 实施任务

- [x] 定义 critical 审计问题、冻结摘要与审计报告 Schema
- [x] 实现规则表驱动的 severity 赋值与证据审计
- [x] 实现冻结合并（approve/needs_change）、哈希绑定与备注拼接
- [x] 新增冻结 CLI，拒绝覆盖已有产物
- [x] 增加规则表、审计、边界、不变量与 CLI 测试（18 用例）
- [x] 冻结 `domain-label-gold-v2` 并生成 critical 审计报告
- [x] 跑首个独立泛化评估并如实记录零覆盖基线
- [x] 在功能文档、README、项目指南和开发日志中记录边界
- [ ] 构建 AI 标签生成管线并在 24 首上生成、审核标签
- [ ] 重算独立泛化指标并决定是否接入在线标签策略

## 14. 决策与变更记录

| 日期 | 决策或变化 | 原因 |
| --- | --- | --- |
| 2026-10-17 | severity 由规则表按（维度，标签名）确定性赋值 | 逐首主观判断不可复现，规则化后跨版本可比 |
| 2026-10-17 | critical 只放 imagery 六项与 theme 五项 | 高精度低歧义的白名单；emotion/allusion 边界主观，放 normal |
| 2026-10-17 | 月加排除表（岁月/月余/非十五月份），鸿雁排除雁门等地名 | 单字触发会把非意象场景误判为 critical |
| 2026-10-17 | 规则表只做审计不做冻结门槛 | 审计如实暴露软肋（羁旅），阻塞冻结会逼出假规则 |
| 2026-10-17 | 冻结产物复用采样清单正文哈希 | 金标准与清单逐首绑定，诗歌改版即失效 |
| 2026-10-17 | 零覆盖基如实记录为“覆盖基线” | 数据库无任何 AI 标签分配，0.0 是真实状态而非失败 |
| 2026-10-17 | 不构建 AI 标签生成管线 | 属独立大切片，先冻结与评估，再单独设计生成质量 |
