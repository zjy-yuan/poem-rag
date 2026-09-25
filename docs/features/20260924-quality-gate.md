# 质量门禁与 v3 基线冻结

> 状态：已实现
> 创建日期：2026-09-24
> 最近更新：2026-09-24
> 关联任务：冻结当前 RAG 回归基线，建立本地和 CI 的统一质量门禁

## 1. 背景与问题

当前后端、前端、RAG 评估和功能文档已经形成完整链路，但质量检查仍依赖人工记忆不同命令。
同时，评估目录中同时存在正式报告和 `_probe/profile` 临时诊断产物，容易在后续提交时
把一次性实验结果与稳定基线混在一起。

## 2. 目标

- 提供一个本地一键质量门禁，覆盖后端 lint/test 和前端 typecheck/test/build。
- 在 GitHub Actions 中执行同一组基础门禁。
- 后端 pytest 使用项目内临时目录并禁用缓存，避免受系统临时目录权限影响。
- 明确临时诊断产物不进入正式版本基线。
- 冻结当前 v3 回归状态，后续 RAG 行为变更必须通过新的独立 holdout 复核。

## 3. 非目标

- 不修改检索、生成、Prompt、阈值、SSE 或数据库契约。
- 不在 CI 中调用真实 Qwen、DeepSeek、MySQL、Redis 或 Qdrant。
- 不把健康检查或单元测试替代真实 RAG 评估。

## 4. 验收标准

| 场景 | 操作 | 预期结果 |
| --- | --- | --- |
| 本地全量验证 | 运行 `.\scripts\verify.ps1` | 后端与前端门禁依次通过 |
| 后端单独验证 | 运行 `.\scripts\verify.ps1 -BackendOnly` | Ruff 与 pytest 通过 |
| 前端单独验证 | 运行 `.\scripts\verify.ps1 -FrontendOnly` | 类型检查、Vitest 和生产构建通过 |
| CI 推送验证 | push 或 pull request | backend 与 frontend job 并行执行 |
| 诊断产物管理 | 生成 `_probe_*` 或 `profile_*` 报告 | 文件保留在本机，不进入 Git 基线 |

## 5. 方案概览

`scripts/verify.ps1` 是本地唯一入口，CI 使用等价的独立 job。正式评估数据集、最终报告和
设计文档继续纳入版本管理；可重复生成的临时检索剖析和 probe 报告不纳入。

## 6. 接口与契约

不涉及公开 HTTP、SSE、数据库或配置契约变化。

## 7. 数据设计

不涉及数据库迁移。`.gitignore` 新增以下诊断产物规则：

- `apps/api/scripts/_probe_*.py`
- `data/eval/_probe_*.json`
- `data/eval/reports/_probe_*.json`
- `data/eval/reports/profile_*.json`

正式 `retrieval_holdout_1000_v1/v2/v3.json`、`generation_holdout_1000_v1/v2/v3.json`
和对应最终报告继续版本化。

四个 holdout 金标准与转换后语料的对照测试需要本机生成
`data/import/generated/chinese-gushiwen-1000-v2.json`。该文件包含第三方正文且被 Git
忽略，因此全新 checkout 或 CI 中这些对照测试会明确跳过；本地存在语料时仍执行完整校验。

## 8. 后端与前端设计

质量门禁只调用现有测试和构建命令，不新增应用代码或运行时依赖。本地 pytest 临时文件
写入 `.verify-tmp/pytest/`，该目录不进入 Git。

## 9. RAG 与评估

本轮不重跑或修改 v3 结果。v3 继续作为冻结回归集；如果未来修改实现并需要归因，
必须创建 v4 独立 holdout，不能把 v3 重新当作未观察集。

## 10. 测试计划

| 层级 | 覆盖内容 |
| --- | --- |
| 本地脚本 | 后端测试、Ruff、前端 typecheck、Vitest、生产构建 |
| CI | 与本地脚本等价的后端和前端 job |
| 回归边界 | v3 数据集与最终报告不修改 |

## 11. 风险与回滚

- CI 只覆盖静态和自动化测试，不能证明真实模型质量。
- 未生成第三方语料时，四项“金标准仍在转换后语料中”校验会跳过，CI 不能替代本地扩库回归。
- 首次新增 GitHub Actions 时，仓库远程配置和 CI 运行权限可能需要单独确认。
- 回滚只需删除 `scripts/verify.ps1`、CI workflow 和 `.gitignore` 新增规则，不影响业务代码。

## 12. 实施任务

- [x] 新增本地一键验证脚本
- [x] 新增 GitHub Actions 后端与前端门禁
- [x] 区分离线诊断产物和正式基线
- [x] 更新开发流程与项目说明

## 13. 决策与变更记录

| 日期 | 决策或变化 | 原因 |
| --- | --- | --- |
| 2026-09-24 | 建立统一质量门禁 | 避免依赖人工记忆命令，并让提交前验证可重复 |
| 2026-09-24 | probe/profile 产物不进入 Git 基线 | 降低仓库噪声，正式报告仍可重建和追踪 |
| 2026-09-24 | 不修改 v3 或在线实现 | 保护当前已观察回归基线的独立性 |
