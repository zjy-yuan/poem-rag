# `aopao/chinese-gushiwen` 1000 首真实语料扩库

> 状态：已实现（当前真实语料快照）
> 创建日期：2026-09-23
> 最近更新：2026-09-23
> 关联任务：把固定来源语料从 100 首扩展到 1000 首，验证筛选、导入、切块、向量索引和扩库后的 RAG 退化

> 后续更新（2026-09-23）：本文件保留扩库当时“不切换在线策略”的历史决策和评估。
> 随后新建独立 1000 首 holdout，在线问答已切换到
> `expanded-lexical-v1 + Dense + RRF`，并加入基础设施故障降级。当前实现、指标和
> 回滚方式见 `20260923-independent-1000-holdout.md`。

## 1. 背景与问题

100 首语料验证了固定来源的完整闭环，但样本仍不足以观察候选规模增长后的检索歧义。
本轮直接复用固定 commit 的 10 个 `guwen*.json` 分片，不新增爬虫，不改变数据库契约，
重点回答以下问题：

1. 分片转换、跨分片筛选和分层抽样是否可重复。
2. 1000 首真实诗词能否通过现有导入、版本、注释和切块链路稳定入库。
3. 扩库后 Qwen Embedding 和 Qdrant 索引是否仍能完成，失败是否能被报告。
4. 原 50 条开放语料检索集在 1000 首候选池上会暴露哪些退化。
5. 当前 28 条生成 holdout 在扩库后是否仍能保持正确回答和稳定拒答。

## 2. 目标

1. 固定来源 commit，记录 10 个输入分片的文件名、URL、SHA256 和记录数。
2. 只保留诗词，排除非诗词、明确文言文和低置信度记录，再做来源内去重。
3. 使用 `dynasty_and_content_length_stratified_v1` 从 5875 条唯一候选中选择 1000 首。
4. 复用 `remark`、`translation`、`shangxi` 到注释、译文、赏析的字段映射。
5. 通过现有 `import_corpus.py --rebuild-chunks` 和 `index_chunks.py --all-pending` 完成入库与索引。
6. 记录扩库后的检索冲击、生成 holdout 和真实库规模，不把旧指标继续当作当前指标。
7. 保持第三方正文、原始分片和含正文评估报告不进入 Git。

## 3. 非目标

1. 不实现网站爬虫、站点适配器或增量同步。
2. 不导入全部 10000 条，也不承诺当前样本代表全量开放语料。
3. 不实现跨来源作品合并、作者消歧和版本差异审核。
4. 不自动补写缺失的注释、译文或赏析。
5. 不切换在线问答检索策略，不把扩库评估直接当成线上配置变更。
6. 不根据同一批 50 条观察集继续调阈值或堆叠局部词典。

## 4. 固定来源

| 项目 | 值 |
| --- | --- |
| 来源 | `aopao/chinese-gushiwen` |
| 固定 commit | `c2345d0abf2404b8b3601e4afc2e8fd12f90d6c8` |
| 输入目录 | `data/raw/aopao-chinese-gushiwen-c2345d0/` |
| 输入文件 | `guwen0-1000.json` 至 `guwen9001-10000.json`，共 10 个分片 |
| 数据集版本 | `chinese-gushiwen-c2345d0-guwen-10000-v2` |
| `source_key` | `aopao-chinese-gushiwen` |
| 转换 manifest | `data/import/reports/chinese-gushiwen-1000-v2.manifest.json` |
| 导入报告 | `data/import/reports/chinese-gushiwen-1000-v2.import.json` |

原始分片、转换后的完整正文、manifest 和导入报告均位于 Git 忽略目录。仓库只保留
转换代码、方法文档和不包含第三方正文的摘要。

## 5. 筛选与转换结果

输入和筛选结果：

| 阶段 | 数量 |
| --- | ---: |
| 原始记录 | 10000 |
| 排除非诗词 | 4088 |
| 排除明确文言文 | 203 |
| 排除低置信度记录 | 3885 |
| 来源内重复候选 | 37 |
| 候选唯一诗词 | 5875 |
| 最终选择 | 1000 |

选择分布：

| 维度 | 分布 |
| --- | --- |
| 朝代 | 宋 435、唐 206、明 149、清 89、元 28、先秦 20、近代 16、当代 15、南北朝 11、魏晋 9、汉 7、五代 6、金 3、隋 3、现代 2、未知 1 |
| 长度 | short 466、medium 458、long 76 |
| 注释 | 624 首 |
| 译文 | 664 首 |
| 赏析 | 537 首 |

转换器默认 `publish=false`。本轮为了公开浏览和在线检索验证显式使用 `--publish`，
正式批量导入仍应先预检，再决定是否发布。

## 6. 导入、切块与索引

复现命令：

```powershell
.\.venv\Scripts\python.exe apps\api\scripts\convert_chinese_gushiwen.py `
  --input-dir data\raw\aopao-chinese-gushiwen-c2345d0 `
  --limit 1000 `
  --publish

.\.venv\Scripts\python.exe apps\api\scripts\import_corpus.py `
  --input data\import\generated\chinese-gushiwen-1000-v2.json `
  --report data\import\reports\chinese-gushiwen-1000-v2.import.json `
  --rebuild-chunks

.\.venv\Scripts\python.exe apps\api\scripts\index_chunks.py --all-pending
```

实际结果：

| 指标 | 结果 |
| --- | ---: |
| 导入总数 | 1000 |
| 新建作品 | 902 |
| 更新作品 | 0 |
| 未变化 | 98 |
| 失败 | 0 |
| MySQL 已发布作品 | 1008 |
| MySQL 作品版本 | 1009 |
| MySQL chunks | 12415 |
| ready chunks | 12415 |
| pending chunks | 0 |
| 已写入 `vector_id` 的 chunks | 12415 |
| 本轮索引版本 | 902 |
| `poem_index_runs` 累计成功 / 失败 | 1009 / 0 |
| Qdrant collection | `poem_chunks_v1` |
| Qdrant points | 12415 |
| Qdrant 状态 | `green` |

`indexed_vectors_count=0` 仍不能单独判定为故障。当前 collection 的
`indexing_threshold=10000`，查询在扩库后仍可正常执行；后续需要用延迟和召回验证
是否需要在更大规模前调整 HNSW 参数，不能直接修改正式 collection。

## 7. 检索评估

原 50 条 `open-corpus-100-v1` 已在 1000 首候选池上重跑。它现在只能作为
“扩库分布冲击回归”，不能证明 1000 首语料的泛化能力，也不能继续作为阈值调参集。
环境为真实 MySQL、Qdrant `poem_chunks_v1` 和 Qwen `text-embedding-v4`，Top-5：

| 策略 | 通过 | Recall@5 | MRR | 无答案准确率 | 平均延迟 |
| --- | ---: | ---: | ---: | ---: | ---: |
| `lexical-baseline-v1` | 36/50 | 0.678571 | 0.638889 | 1.000000 | 114.194 ms |
| `expanded-lexical-v1` | 34/50 | 0.761905 | 0.662698 | 0.375000 | 182.992 ms |
| `dense-baseline-v1` | 37/50 | 0.880952 | 0.815476 | 0.000000 | 176.095 ms |
| `hybrid-rrf-v1 + 0.50` | 42/50 | 0.940476 | 0.876984 | 0.375000 | 211.241 ms |
| `hybrid-rrf-v1 + 0.60` | 43/50 | 0.952381 | 0.888889 | 0.500000 | 218.330 ms |

100 首时 `hybrid-rrf-v1 + 0.60` 为 47/50、Recall@5 `0.988095`、MRR `0.927381`、
无答案准确率 `0.75`。新增语料明显挤压多证据问题和领域内拒答，当前在线策略仍是
`expanded-lexical-v1 + parent_context + assess`，不能因为 Dense 或 Hybrid 的单次
召回更高就直接切换。

## 8. 生成评估

在 1000 首真实语料上运行默认 28 条生成 holdout，环境为真实 MySQL、Qdrant、
Qwen Embedding 和 `deepseek-chat`：

| 指标 | 结果 |
| --- | ---: |
| 通过 | 28/28 |
| 有答案准确率 | 1.000000 |
| 拒答准确率 | 1.000000 |
| 拒答 P/R/F1 | 1.000000 / 1.000000 / 1.000000 |
| 引用精确率 | 0.933333 |
| 引用召回率 | 1.000000 |
| 平均延迟 | 2198.164 ms |
| P95 延迟 | 4325.333 ms |

生成通过不代表检索层已经泛化。引用精确率下降到 `0.933333`，说明回答正确时仍会出现
额外引用噪声；28 条样本也经过针对性调参，不能替代独立 holdout。

## 9. 接口与契约

本轮没有新增或修改公开 HTTP 接口、SSE 事件和数据库迁移。

新增的是数据转换 CLI 能力：

1. `--input-dir` 支持按自然排序读取目录中的 `guwen*.json` 分片。
2. `--limit` 控制最终分层选择数量。
3. `--output` 和 `--manifest` 可覆盖默认报告路径。
4. 使用 `--input-dir` 时，未显式传入 `--limit` 的默认值为 1000。
5. 单文件模式仍保留，未显式传入 `--limit` 时默认值为 100。

## 10. 数据设计

本轮没有新增表或字段，复用现有模型：

1. `poem_sources` 记录固定来源和分片证据。
2. `poem_versions` 保存不可变正文版本和内容哈希。
3. `poem_annotations` 保存注释、译文和赏析。
4. `poem_chunks` 保存 `structural-v1` 切块和向量索引状态。
5. `poem_index_runs` 保存版本级索引运行状态。

导入仍以 `external_id` 保证幂等。98 条未变化记录来自此前 100 首验证的重叠部分，
说明同一固定来源重复导入不会重复创建作品或版本。

## 11. 风险与回滚

1. 来源未声明 LICENSE，只适合学习和非公开技术展示。
2. 当前 1000 首是固定来源的分层样本，不代表全量开放语料。
3. 跨来源重复作品仍可能存在，种子作品和导入作品的 ID 歧义尚未自动合并。
4. 原 50 条检索集和 28 条生成集已经用于多轮调参，必须补充独立 holdout。
5. 扩库后 Qdrant 的全扫描与 HNSW 阈值需要继续做性能和召回验证。
6. 出现质量或授权问题时，应按来源和数据集版本撤销发布，再重建 chunks 与向量。

## 12. 验证结果

| 层级 | 结果 |
| --- | --- |
| 后端测试 | `135 passed, 3 warnings` |
| Ruff | `All checks passed!` |
| 转换专项测试 | 覆盖多分片、筛选、去重和分层抽样 |
| 真实导入 | 902 created、98 unchanged、0 failed |
| 真实索引 | 902 个版本、10346 个新增 chunks、0 failed |
| 检索评估 | 五组 1000 首候选池对照，指标见上表 |
| 生成评估 | 28/28，拒答 7/7，引用精确率 `0.933333` |

本轮文档收口没有修改运行时代码，因此没有重复执行前端和完整后端测试。上述测试结果
来自扩库实现与真实评估阶段。

## 13. 后续方向

1. 新建独立于当前 50/28 条样本的 1000 首 holdout。（已完成，见
   `20260923-independent-1000-holdout.md`）
2. 重点覆盖多证据问题、领域内缺属性问题、引用噪声和结构化过滤。
3. 对比在线 `expanded-lexical-v1`、`hybrid-rrf-v1 + 0.60` 和 Rerank。
4. 为作者、朝代、体裁和标签设计结构化过滤。
5. 在独立 holdout 上验证阈值、Embedding 维度和 Qdrant 参数后再决定在线切换。

## 14. 决策与变更记录

| 日期 | 决策或变化 | 原因 |
| --- | --- | --- |
| 2026-09-23 | 从 100 首扩展到固定来源的 1000 首 | 观察候选规模增长后的真实退化 |
| 2026-09-23 | 使用 10 个分片和跨分片去重 | 保持来源固定、可追溯，不引入爬虫漂移 |
| 2026-09-23 | 保留 98 条未变化记录 | 验证固定来源重复导入的幂等性 |
| 2026-09-23 | 旧 50 条集只作为扩库冲击回归 | 避免继续在同一观察集上调参 |
| 2026-09-23 | 在线检索策略不切换 | Dense/Hybrid 召回更高，但领域内拒答仍不足 |
| 2026-09-23 | 记录引用精确率下降 | 回答正确不代表引用集合已经精确 |
