# 诗词级父级上下文补全

> 状态：已实现  
> 创建日期：2026-09-20  
> 最近更新：2026-09-20  
> 关联任务：在线问答证据组装、生成层评估失败样本修复

## 1. 背景与问题

生成层评估的 12 条样本中，`gen-shuidiaogetou-moon`
（“《水调歌头·明月几时有》借明月表达了什么情感？”）稳定失败：
`assess` 返回 `missing_fact` 并走拒答，但语料里确实有依据。

真实库 dump 显示根因在证据组装，而不是判定逻辑：

1. 版本 `7` 有 1 条诗词级 chunk（`85`）和 8 条行级 chunk（`86`-`93`）。
2. 行级 chunk 因为标题短语权重和 RRF 融合分更高，全部排在诗词级 chunk 前面。
3. 在线 `chat_retrieval_limit=5` 时只有上阕 5 行入选，下阕
   （“不应有恨”“但愿人长久”）从未进入上下文。

也就是说：检索“找对了作品”，但交给模型的是半篇作品。诗词是短文本、
意象密集，只给上阕会直接改变可答性判定结果。

## 2. 目标

- 行级命中时也能把整篇作品交给 `assess` 和生成节点。
- 不改变公开 HTTP 接口、SSE 事件结构和引用编号语义。
- 在线问答与离线生成评估共用同一条检索组装链，避免评估和线上分叉。

## 3. 非目标

- 不修改 `assess` 的判定 Prompt 和判定 Schema。
- 不引入 Rerank、Cross-Encoder 或新的向量模型。
- 不做长诗的自动分段裁剪；开放语料下的 token 预算仍属后续工作。
- 不把父级上下文写入向量库或改变 chunk 切分策略。

## 4. 用户场景与验收标准

| 场景 | 操作 | 预期结果 |
| --- | --- | --- |
| 行级命中短篇作品 | 提问只命中下阕某一句 | 该作品的诗词级 chunk 一并进入上下文 |
| 已命中诗词级 chunk | 提问直接命中整篇作品 | 不重复追加同版本上下文 |
| 多作品命中 | 一次提问命中多首作品 | 按作品最佳排名顺序追加，最多 3 条 |
| 上下文预算为 0 | 显式关闭父级上下文 | 行为与补全前完全一致 |
| 引用可追溯 | 模型引用 `[6]` | `rank=6` 指向真实 chunk，编号连续无空洞 |
| 引用展示 | 前端读取 `retrieval` 事件 | `selected_count` 允许大于公开检索 `limit` |

## 5. 方案概览

```text
RagChatGraph
  -> PoemContextRetrievalService（装饰器）
       -> ExpandedRetrievalService（expanded-lexical-v1）
            -> RetrievalService（词法检索）
       -> ChunkRepository.list_poem_chunks_by_version_ids
  -> assess / generate
```

关键取舍：

1. **装饰器而不是改排序。** 父级上下文是“证据完整性”问题，不是“相关性排序”问题。
   把它追加在排序结果之后，可以修复证据缺失而不污染现有相关性分数。
2. **只在缺失时补。** 如果该版本已经有诗词级 chunk 入选，不再重复追加。
3. **最多 3 条。** 限制上下文膨胀，同时覆盖常见的多作品对比问题。
4. **分数沿用该作品最高分。** 避免引入一个没有评估依据的新分数体系。
5. **协议化依赖。** `RagChatGraph` 依赖 `EvidenceRetriever` 协议，装饰器和具体检索
   实现可以自由组合。

## 6. 接口与契约

不新增 HTTP 接口，不改请求参数。唯一对外可见变化是 SSE `retrieval` 事件：

```text
event: retrieval
data: {"candidate_count":9,"selected_count":6,"strategy":"expanded-lexical-v1"}
```

规则：

1. `selected_count` 可以大于公开检索接口的 `limit`，因为它统计的是送入模型的证据数。
2. `strategy` 和 `candidate_count` 由内层检索透传，装饰器不重写。
3. `citation` 事件的 `rank` 仍然从 1 连续编号，父级上下文排在末尾。
4. 新匹配标记 `parent_context` 出现在内部证据对象上，不进入公开检索接口响应。

## 7. 数据设计

不新增表和字段。只新增一个只读查询：

```text
ChunkRepository.list_poem_chunks_by_version_ids(version_ids)
```

约束：

1. 只返回 `published` 且未软删除作品的当前版本。
2. 只返回 `pending`/`ready` 状态的诗词级 chunk。
3. 一次查询批量取回多个版本，避免按版本循环查询。

## 8. 后端设计

```text
app/services/evidence_context.py     PoemContextRetrievalService
app/services/retrieval.py            EvidenceRetriever 协议、to_retrieval_evidence
app/services/chat.py                 build_chat_retrieval 组装函数
app/repositories/chunks.py           批量诗词级 chunk 查询
app/ai/graphs/rag.py                 依赖协议而非具体 Service
```

行为细节：

1. 版本顺序按“该作品最佳排名”决定，保证强相关作品的上下文先进入。
2. 已入选的 chunk 不会重复追加。
3. `max_context_chunks=0` 时直接返回内层结果，便于灰度关闭。
4. `max_context_chunks<0` 在构造时抛 `ValueError`，属于编程错误而非运行时降级。

## 9. 前端设计

前端无需改动。问答页文案“已从 N 条候选中选取 M 条依据”在 `M` 大于检索 `limit`
时仍然成立。

## 10. RAG 与评估

1. 在线检索策略名保持 `expanded-lexical-v1`，父级上下文是组装层行为，不是新策略。
2. 生成层评估使用 `build_chat_retrieval`，因此评估路径与在线路径一致。
3. 检索层评估脚本尚未接入该装饰器，检索指标不体现本次改动；本次效果证据来自
   生成层报告 `data/eval/reports/generation_rag_v1_after_parent_context_20260920.json`。
4. 生成层结果：`12/12`，引用精确率/召回率 `1.0`，拒答 F1 `1.0`。

## 11. 测试计划

| 层级 | 覆盖内容 |
| --- | --- |
| 单元测试 | 上下文追加、已存在诗词 chunk 时跳过、预算上限、预算为 0、负预算校验 |
| 单元测试 | 单变体改写结果按 `limit` 截断 |
| 集成测试 | 7 行作品的流式问答：`candidate_count=8`、`selected_count=6`、Prompt 含完整作品 |
| 真实评估 | 真实 MySQL + DeepSeek 生成评估 `12/12` |

## 12. 风险与回滚

1. 长诗和组诗在开放语料下可能显著膨胀 Prompt，需要按 token 预算重新设定规则。
2. 追加项不参与排序，未来引入 Rerank 时必须重新设计两者的关系。
3. 检索层评估口径与在线链路仍不一致，需要补齐组装链评估入口。
4. 回滚方式：把 `build_chat_retrieval` 换成 `ExpandedRetrievalService` 直接调用，
   或把 `max_context_chunks` 设为 `0`，不需要改数据库和前端。

## 13. 实施任务

- [x] 文档与契约
- [x] 仓储批量查询与协议抽取
- [x] 装饰器实现与在线组装
- [x] 单元测试和集成测试
- [x] 真实生成层评估
- [ ] 检索层评估覆盖组装链
- [ ] 开放语料下的 token 预算与长诗策略

## 14. 决策与变更记录

| 日期 | 决策或变化 | 原因 |
| --- | --- | --- |
| 2026-09-20 | 用装饰器追加父级上下文，而不是修改相关性排序 | 这是证据完整性问题，不是相关性排序问题 |
| 2026-09-20 | 不改 `assess` 判定 Prompt | 判定逻辑正确，改判定会掩盖证据缺失 |
| 2026-09-20 | 上下文最多 3 条并排在结果末尾 | 控制 Prompt 膨胀，同时保持引用 `rank` 连续 |
| 2026-09-20 | 在线问答与生成评估共用 `build_chat_retrieval` | 评估路径必须和用户路径一致 |
