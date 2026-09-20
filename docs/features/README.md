# 功能设计文档

> 适用于跨模块、跨数据库、API 或 RAG 策略的功能。  
> 小型修复直接记录到 `DEVELOPMENT_LOG.md`，无需强行创建功能文档。

## 1. 使用方式

1. 复制下面模板到 `docs/features/YYYYMMDD-short-name.md`。
2. 在编码前填写范围、契约、数据和验收标准。
3. 实现过程中更新状态和关键决策，不删除已经验证过的负面结论。
4. 功能验收后链接到对应开发日志和评估报告。

文件示例：

```text
docs/features/20260920-rag-corpus-model.md
docs/features/20260922-multi-granularity-chunking.md
docs/features/20260923-index-run-metadata.md
docs/features/20260925-hybrid-retrieval-baseline.md
```

当前功能文档：

1. `20260919-rag-corpus-model.md`
2. `20260919-structural-chunking.md`
3. `20260919-index-run-metadata.md`
4. `20260919-mysql-retrieval-baseline.md`
5. `20260919-retrieval-evaluation.md`
6. `20260919-open-licensed-corpus-import.md`
7. `20260919-qwen-embedding-provider.md`
8. `20260919-qdrant-vector-store.md`
9. `20260919-dense-retrieval.md`
10. `20260919-hybrid-rrf-retrieval.md`
11. `20260919-query-expansion-retrieval.md`
12. `20260920-chat-rag-sse.md`
13. `20260920-evidence-relevance-floor.md`
14. `20260920-answerability-evaluation-v2.md`

## 2. 模板

```markdown
# 功能名称

> 状态：设计  
> 创建日期：YYYY-MM-DD  
> 最近更新：YYYY-MM-DD  
> 关联任务：...

## 1. 背景与问题

用户或开发流程当前遇到什么问题？为什么现在要做？

## 2. 目标

- ...

## 3. 非目标

- ...

## 4. 用户场景与验收标准

| 场景 | 操作 | 预期结果 |
| --- | --- | --- |
| ... | ... | ... |

## 5. 方案概览

描述数据流、模块边界和关键选择。存在多个方案时列出取舍，不只写最终答案。

## 6. 接口与契约

- 新增或修改的 API。
- 请求、响应和错误码。
- 权限、幂等、并发、超时和重试。
- 完成后需要同步到 `FRONTEND_BACKEND_CONTRACT.md` 的内容。

## 7. 数据设计

- 表、字段、关系、索引和约束。
- 迁移和回滚。
- 历史数据与种子数据。
- 删除、版本和审计策略。

## 8. 后端设计

- Router、Dependency、Service、Repository。
- 事务边界、状态机和失败恢复。
- 日志、指标和可观测性。

## 9. 前端设计

- 页面、路由、组件和状态。
- 加载、空态、错误和无权限状态。
- 移动端和桌面端交互。

## 10. RAG 与评估

如不涉及 RAG，写“不涉及”。

包括语料版本、切块、模型、检索、重排、Prompt、引用规则、指标和成本。

## 11. 测试计划

| 层级 | 覆盖内容 |
| --- | --- |
| 单元测试 | ... |
| 集成测试 | ... |
| 契约测试 | ... |
| E2E | ... |
| 评估 | ... |

## 12. 风险与回滚

- 数据、性能、成本、安全和依赖风险。
- 出现问题时如何回滚或降级。

## 13. 实施任务

- [ ] 文档与契约
- [ ] 迁移
- [ ] 后端
- [ ] 前端
- [ ] 测试与真实联调
- [ ] 评估与复盘

## 14. 决策与变更记录

| 日期 | 决策或变化 | 原因 |
| --- | --- | --- |
| YYYY-MM-DD | ... | ... |
```
