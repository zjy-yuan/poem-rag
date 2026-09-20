# MySQL 可解释检索基线 `lexical-baseline-v1`

> 状态：已实现  
> 创建日期：2026-09-19  
> 最近更新：2026-09-19  
> 关联任务：在接入 Embedding 与 Qdrant 前建立可复现、可解释的检索事实层

## 1. 背景与问题

`poem_chunks` 已经保存 poem、line 和 note 三种粒度，但公开搜索仍停留在整首作品的目录查询。它不能回答“命中哪一句”“命中赏析还是正文”“为什么排在前面”，因此无法为后续 RAG 引用和评估提供稳定基线。

直接跳到 Qwen Embedding 和 Qdrant 会带来两个问题：

1. 无法区分召回失败来自切块、词法匹配还是向量模型。
2. 没有可解释的对照结果，无法证明向量检索或混合检索是否真的改善。

本切片先实现不依赖模型和向量库的词法基线。

## 2. 目标

1. 从当前有效版本的 chunks 中检索证据，而不是重新扫描 `poems.content`。
2. 支持 poem、line、note 粒度过滤。
3. 返回作品、版本、作者、朝代、行号、注释类型和切块策略。
4. 返回稳定的归一化得分和 `match_types`，解释每条证据为什么命中。
5. 只返回已发布且未删除作品、当前版本、`pending/ready` chunks 和有效注释。
6. 使用 `LIKE` 转义避免用户输入中的 `%`、`_` 和反斜杠被当作通配符。
7. 建立后续 Dense、Sparse、RRF 和 Rerank 的共同对照基线。

## 3. 非目标

1. 不调用 Qwen Embedding、DeepSeek、Qdrant 或 LangGraph。
2. 不实现中文分词、BM25、MySQL ngram FULLTEXT 或拼音检索。
3. 不实现查询改写、同义词扩展、意象映射和重排模型。
4. 不生成自然语言答案，不实现 SSE。
5. 不声称该基线已经提升问答准确率。

## 4. 用户场景与验收标准

| 场景 | 操作 | 预期结果 |
| --- | --- | --- |
| 诗句检索 | 查询“床前明月光” | 返回对应 line chunk，精确匹配得分为 `1.0` |
| 整篇检索 | 查询短诗中的连续文本 | 返回 poem chunk 和匹配的 line chunk |
| 赏析检索 | 查询赏析中的短语 | 返回 note chunk，并带 annotation 类型 |
| 按作者过滤 | 查询标题并传 `author_id` | 只返回该作者的当前有效 chunks |
| 草稿隔离 | 查询草稿正文 | 不返回任何证据 |
| 软删除隔离 | 查询已删除作品正文 | 不返回任何证据 |
| 旧版本隔离 | 编辑后查询旧版本专属文本 | 不返回旧版本 chunks |
| 禁用片段 | chunk 状态为 `disabled` | 不返回该片段 |
| 通配符 | 查询包含 `%` 或 `_` 的文本 | 按普通字符匹配，不扩大结果 |
| 空白查询 | 查询仅含空白 | 返回 `422 VALIDATION_ERROR` |

## 5. 方案概览

```text
GET /api/v1/search/evidence
  -> Router 校验 q、limit、granularity 和过滤条件
  -> RetrievalService 归一化查询
  -> ChunkRepository 从当前版本 chunks 召回候选
  -> Service 计算可解释得分、匹配类型和稳定排序
  -> 返回带出处信息的 RetrievalEvidence[]
```

### 5.1 召回规则

1. 只连接 `poems.status=published` 且 `deleted_at IS NULL` 的作品。
2. 只连接 `poem_versions.version_no = poems.version_no` 的当前版本。
3. chunk 状态只允许 `pending` 和 `ready`，排除 `failed` 和 `disabled`。
4. note chunk 必须关联 `published` 注释；正文 chunk 不要求 annotation。
5. 查询同时匹配 `normalized_text`、标题、作者和朝代。
6. 候选先按数据库主键稳定召回，再由 Service 评分和排序。

### 5.2 得分规则

| 匹配 | 基础分 | 说明 |
| --- | --- | --- |
| `chunk_exact` | `1.0` | 归一化后 chunk 文本与查询完全一致 |
| `chunk_phrase` | `0.7 + 0.25 * 覆盖率` | 查询是 chunk 子串，覆盖率越高分越高 |
| `title_phrase` | `0.6` | 标题包含查询 |
| `author_phrase` | `0.55` | 作者包含查询 |
| `dynasty_phrase` | `0.5` | 朝代包含查询 |

同一证据可包含多个 `match_types`，最终得分取最高分。排序为“得分降序 -> line/poem/note 粒度优先级 -> chunk_id 升序”。这是当前基线的确定性规则，不代表最终相关性模型。

## 6. 接口与契约

```http
GET /api/v1/search/evidence?q=床前明月光&limit=10&granularity=line&author_id=12
```

请求参数：

| 参数 | 类型 | 约束 | 说明 |
| --- | --- | --- | --- |
| `q` | string | 1 至 200 字符 | 查询文本，空白值由 Service 返回 `422` |
| `limit` | int | 1 至 50，默认 10 | 最终返回证据数 |
| `granularity` | string[] | `poem/line/note` | 可重复传入，省略表示全部 |
| `author_id` | int | 可选 | 作者过滤 |
| `dynasty_id` | int | 可选 | 朝代过滤 |

响应 data 元素：

```json
{
  "chunk_id": 101,
  "poem_id": 8,
  "poem_version_id": 8,
  "annotation_id": null,
  "annotation_type": null,
  "title": "静夜思",
  "author_id": 2,
  "author_name": "李白",
  "dynasty_id": 1,
  "dynasty_name": "唐",
  "granularity": "line",
  "chunk_index": 0,
  "text": "床前明月光，疑是地上霜。",
  "line_start": 1,
  "line_end": 1,
  "chunk_strategy": "structural-v1",
  "status": "pending",
  "score": 1.0,
  "match_types": ["chunk_exact"],
  "published_at": "2026-09-19T00:00:00Z"
}
```

meta 包含 `strategy`、原始查询、归一化查询、评分候选数、`limit` 和过滤条件。

该接口是公开只读接口，不暴露 `vector_id`、Embedding 模型或数据库内部信息。

## 7. 数据设计

不新增表、字段或迁移。查询使用现有：

1. `poems`：发布状态、软删除和当前版本号。
2. `poem_versions`：不可变版本归属。
3. `poem_chunks`：检索文本、粒度、行号和索引状态。
4. `poem_annotations`：note chunk 的发布状态和注释类型。
5. `authors`、`dynasties`：过滤和可解释元数据。

当前数据量下先使用 `LIKE` 扫描候选，不增加 ngram FULLTEXT 依赖。真实语料规模、中文分词方案和延迟数据明确后，再评估 MySQL FULLTEXT、独立搜索引擎或稀疏向量。

## 8. 后端设计

新增 `app/repositories/chunks.py`：

1. 定义 `ChunkSearchCandidate` 查询投影。
2. 负责当前版本、可见性和过滤条件。
3. 返回带出处元数据的候选，不计算业务得分。

新增 `app/services/retrieval.py`：

1. 校验归一化后查询非空。
2. 调用 Repository 召回候选。
3. 计算得分、匹配类型和稳定排序。
4. 将候选转换为 `RetrievalEvidence`。

`search.py` 只负责 HTTP 参数和响应 Envelope。现有 `GET /api/v1/search` 目录搜索保持不变。

## 9. 前端设计

本切片不新增页面。后续搜索页可先展示证据文本、出处、粒度和匹配原因，用于开发调试和评估；面向普通用户的展示在生成链路稳定后再设计。

## 10. RAG 与评估

该基线用于回答以下问题：

1. 金标准证据是否在 Top-k 中，计算 Recall@k。
2. 第一个正确证据的位置，计算 MRR。
3. 不同 granularity 是否导致重复或噪声。
4. 后续 Dense 或混合检索是否在相同问题上改善。

当前没有固定评估集和标注证据，因此本轮只验证行为正确性，不记录效果提升数字。

## 11. 测试计划

| 层级 | 覆盖内容 |
| --- | --- |
| 单元测试 | 归一化、通配符转义和评分规则 |
| 集成测试 | line/poem/note 命中、元数据过滤和稳定排序 |
| 契约测试 | Envelope、`422` 空查询、`granularity` 参数 |
| 隔离测试 | 草稿、软删除、旧版本、disabled chunk 不可见 |
| E2E | 本切片不涉及 |
| 评估 | 固定问题和金标准证据建立后执行 |

## 12. 风险与回滚

1. `LIKE` 在大语料上会退化为扫描，不能直接作为最终全文检索方案。
2. 中文没有分词和 BM25，长查询与短句的分数仍需评估校准。
3. 当前 `pending` chunks 可被检索，是模型接入前的开发基线；接入索引后应明确只检索 `ready` 还是允许降级到 `pending`。
4. 标题、作者和朝代命中会给整首作品的多个 chunks 相同元数据分，可能产生重复，需要 Rerank 或按作品聚合。
5. 回滚方式是移除新路由并停止调用 `RetrievalService`；没有数据库迁移需要回滚。

## 13. 实施任务

- [x] 功能设计
- [x] Repository 候选召回
- [x] Service 评分和序列化
- [x] `GET /api/v1/search/evidence`
- [x] 可见性与边界测试
- [x] 回写项目说明、接口契约和开发日志
- [ ] 准备开放许可真实语料
- [ ] 建立固定评估问题和金标准证据
- [ ] 对比 Qwen Embedding、Qdrant 和混合检索

## 14. 决策与变更记录

| 日期 | 决策或变化 | 原因 |
| --- | --- | --- |
| 2026-09-19 | 先做词法证据检索，不直接接向量库 | 建立可解释对照组，区分切块、召回和模型问题 |
| 2026-09-19 | 只检索当前版本和可见作品 | 防止旧版本、草稿和软删除内容通过 RAG 泄漏 |
| 2026-09-19 | 允许 `pending/ready` chunks | 当前真实库 chunks 尚未向量化，需要先验证检索契约 |
| 2026-09-19 | 不启用 MySQL FULLTEXT ngram | 避免在分词、索引维护和部署差异未验证前引入隐式依赖 |
