# 在线问答、LangGraph 与 SSE 闭环

> 状态：已实现  
> 创建日期：2026-09-20  
> 最近更新：2026-09-20  
> 关联任务：最小在线 RAG 闭环、会话持久化、流式回答和引用展示

## 1. 背景与问题

项目此前已经具备诗词目录、结构切块、词法检索、查询改写和内部 Dense/Hybrid 评估，
但用户仍无法在 Web 页面中完成一次可持久化的问答。只展示检索证据不算 RAG 闭环，
还缺少以下能力：

1. 用户会话和消息的长期存储。
2. 查询改写、检索、生成和校验的明确编排。
3. 回答生成过程中的增量输出和引用事件。
4. 无证据时拒绝编造，以及模型失败时的可恢复状态。
5. Vue 页面中的会话切换、停止生成、历史恢复和引用浏览。

## 2. 目标

- 为登录用户提供会话 CRUD 和消息历史读取。
- 使用最小 LangGraph 流程完成
  `rewrite -> retrieve -> generate -> validate`。
- 在线检索固定使用已经评估的 `expanded-lexical-v1`，不把未验证的 Dense/Hybrid
  结果提前接入公开问答。
- 使用可替换的 `ChatModelPort` 接入 DeepSeek OpenAI-compatible 流式接口。
- 通过 POST SSE 输出检索、增量文本、引用、完成和错误事件。
- 将最终回答、状态、模型、延迟和引用快照持久化到 MySQL。
- 无证据时稳定拒答且不调用生成模型。
- Vue 3 页面支持桌面和移动端会话、流式回答、停止生成和引用卡片。

## 3. 非目标

- 不在本切片实现消息反馈、会话检索或自动标题总结。
- 不把在线检索切换为 Dense、Hybrid 或 Rerank。
- 不实现 Redis 短期记忆、分布式流锁或 WebSocket 多端同步。
- 不实现模型路由、多 Agent、工具调用或长期记忆。
- 不宣称真实 DeepSeek 生成质量已经完成验收。

## 4. 用户场景与验收标准

| 场景 | 操作 | 预期结果 |
| --- | --- | --- |
| 创建会话 | 登录用户点击“新会话”并发送问题 | 会话创建成功，路由进入会话详情 |
| 流式回答 | 提问“静夜思里的月亮有什么含义” | 依次收到 `meta`、`retrieval`、`delta` 和引用 |
| 历史恢复 | 刷新或重新进入会话 | 从 MySQL 恢复用户消息、助手回答和引用快照 |
| 无证据 | 提问当前语料完全不存在的内容 | 返回固定拒答，不产生引用，不调用模型 |
| 模型失败 | Provider 超时或返回错误 | 助手消息标记 `failed`，前端展示可理解错误 |
| 停止生成 | 流式过程中点击停止 | 客户端中断，后端尽力保存 `cancelled` 消息 |
| 资源隔离 | 用户访问他人会话 | 返回 `404 CONVERSATION_NOT_FOUND` |
| 未配置模型 | 不存在有效 Chat Provider 时发起问答 | SSE 建立前返回 `503 CHAT_MODEL_NOT_CONFIGURED` |

## 5. 方案概览

```text
Vue ChatView
  -> chatApi.streamMessage()
  -> POST /api/v1/conversations/{id}/messages:stream
  -> ChatService 校验会话所有权和 Provider
  -> 写入 user message 和 streaming assistant message
  -> LangGraph
       rewrite
         -> retrieve: expanded-lexical-v1
         -> generate: DeepSeek Chat Provider
         -> validate
  -> SSE meta / retrieval / delta / citation / done
  -> 提交最终回答、消息状态、延迟和引用快照
```

关键取舍：

1. SSE 使用 POST + `fetch` 流解析，而不是 `EventSource`，因为需要 Bearer Header 和
   JSON 请求体。
2. MySQL 是会话事实来源，SSE 只表达请求过程，不保存历史。
3. 生成前先建立用户消息和助手占位消息，使失败和取消也有审计记录。
4. `citation` 事件携带完整引用快照，前端不依赖当前 chunk 仍存在。
5. Provider 在每次请求结束时关闭，避免依赖注入对象跨请求复用。

## 6. 接口与契约

已实现接口：

```text
POST   /api/v1/conversations
GET    /api/v1/conversations
GET    /api/v1/conversations/{id}
PATCH  /api/v1/conversations/{id}
DELETE /api/v1/conversations/{id}
GET    /api/v1/conversations/{id}/messages
POST   /api/v1/conversations/{id}/messages:stream
```

SSE 事件顺序：

```text
meta -> retrieval -> delta* -> citation* -> done
```

常见模型失败事件：

```text
meta -> retrieval -> error
```

无证据事件：

```text
meta -> retrieval -> delta -> done
```

接口和字段的稳定说明见 `docs/FRONTEND_BACKEND_CONTRACT.md` 第 6.3、10 节。

## 7. 数据设计

迁移：`20260920_0005_create_conversations_and_messages.py`

1. `conversations`：`user_id`、标题、状态、最后消息时间和时间戳。
2. `messages`：角色、内容、流式状态、模型、延迟、错误码和时间戳。
3. `message_citations`：回答与 chunk 的可选关联，以及作品、版本、注释、文本、分数和
   排名快照。

约束：

1. 用户删除时级联删除会话，会话删除时级联删除消息，消息删除时级联删除引用。
2. `chunk_id` 删除时设为 `NULL`，历史引用文本仍保留。
3. 会话所有权查询同时约束 `conversation_id` 和 `user_id`。
4. 助手消息可以有 `streaming`、`completed`、`failed`、`cancelled` 状态。

## 8. 后端设计

主要模块：

```text
app/api/v1/conversations.py
app/schemas/chat.py
app/services/chat.py
app/repositories/conversations.py
app/repositories/messages.py
app/ai/providers/chat.py
app/ai/providers/deepseek.py
app/ai/graphs/rag.py
```

职责边界：

1. Router 只处理 HTTP 参数、鉴权和 `StreamingResponse`。
2. `ChatService` 负责所有权校验、事务、消息状态、SSE 事件映射和 Provider 关闭。
3. `RagChatGraph` 只负责节点状态、检索、生成和最终校验。
4. `ChatModelPort` 隔离供应商协议；当前适配器是 DeepSeek OpenAI-compatible API。
5. Repository 负责会话、消息和引用的查询与写入。

失败处理：

1. Provider 未配置：返回普通 `503` Envelope，不写入消息。
2. 流式模型错误：发送 `error`，消息标记 `failed` 并记录错误码。
3. 客户端取消：捕获 `CancelledError`/`GeneratorExit`，尽力提交 `cancelled` 状态。
4. 数据库提交失败：回滚并记录日志，SSE 发送受控 `INTERNAL_ERROR`。

## 9. 前端设计

主要模块：

```text
src/api/chat.ts
src/features/chat/sse.ts
src/views/ChatView.vue
```

已实现交互：

1. 桌面端会话侧栏和当前问答区；窄屏下会话栏横向滚动。
2. 新建会话、切换会话、删除会话和 URL 路由同步。
3. 原生 `fetch` 读取 SSE，按块解析事件，不依赖 Axios 完整响应。
4. 回答增量更新，停止生成使用 `AbortController`。
5. 引用卡片显示标题、作者、朝代、粒度和原文，并链接到诗词详情。
6. 加载、空会话、失败、取消和未配置模型错误均有页面状态。

## 10. RAG 与评估

当前在线策略：

1. 查询改写：`LexiconQueryRewriter`。
2. 检索：`expanded-lexical-v1`，原查询加领域扩展词，多路召回后做 RRF。
3. 生成上下文：最多 5 条证据，单条最多 1200 字符，总上下文最多 6000 字符。
4. 生成约束：只能依据检索证据回答，不能编造作品、作者、朝代、典故或出处。
5. 校验：空回答失败；有证据时最终回答必须有可追溯引用。
6. 拒答：证据为空时返回“没有在当前诗词库中找到足够依据，暂时无法回答这个问题。”

当前没有把 DeepSeek 生成结果纳入固定评估集，因此只能确认链路和约束，不能声称生成
准确率提升。

## 11. 测试计划

| 层级 | 覆盖内容 |
| --- | --- |
| 单元测试 | 会话 CRUD、所有权、消息序列化、SSE 事件编码、DeepSeek 流解析 |
| 集成测试 | 真实 TestClient + 数据库会话 + Fake Chat Provider 的完整流式回答 |
| 契约测试 | SSE 事件顺序、消息持久化、引用快照、未配置模型错误 |
| 前端测试 | SSE 分块、CRLF、多行 data 和最终事件刷新解析 |
| 真实联调 | 真实 MySQL、真实 HTTP/SSE、注册和会话问答链路；DeepSeek 流式烟测脚本 |
| 评估 | 当前只覆盖检索层，生成质量评估待补充 |

## 12. 风险与回滚

1. 真实 DeepSeek 模型 ID、限流、上下文和网络行为尚未完成账号级联调。
2. 在线检索仍使用规则查询改写和 MySQL 词法检索，开放语料上的召回质量未知。
3. SSE 错误在 HTTP `200` 后通过事件传输，前端必须持续解析终端事件。
4. 客户端断开和进程崩溃之间仍存在消息状态不能及时更新的窗口。
5. 当前没有会话级限流和 Token 成本审计。
6. 回滚时可先禁用 Chat Provider 配置，接口返回 `503`，已有会话和消息数据不受影响。

## 13. 实施任务

- [x] 文档与契约
- [x] 迁移
- [x] 后端
- [x] 前端
- [x] 测试与真实 MySQL 联调
- [x] DeepSeek 流式烟测入口
- [ ] 真实 DeepSeek 生成联调
- [ ] 生成质量与引用忠实度评估

## 14. 决策与变更记录

| 日期 | 决策或变化 | 原因 |
| --- | --- | --- |
| 2026-09-20 | 在线问答固定使用 `expanded-lexical-v1` | 该策略已在同一评估集达到 `27/27`，Dense/Hybrid 尚未完成真实同集比较 |
| 2026-09-20 | 会话与消息写入 MySQL，SSE 只做传输 | 避免连接状态成为长期事实来源 |
| 2026-09-20 | 无证据时不调用模型 | 降低编造和成本风险，确保稳定拒答 |
| 2026-09-20 | 引用保存完整快照并保留可空 `chunk_id` | chunk 重建后历史回答仍可展示 |
| 2026-09-20 | 模型未配置时在 SSE 建立前返回 `503` | 让前端和客户端明确区分配置问题与流式失败 |
