# 前后端框架、路由与响应契约

> 项目：Poem RAG  
> 文档版本：v0.4  
> 创建日期：2026-09-19  
> 最后更新：2026-09-20  
> 文档状态：前后端目标接口的唯一事实源  
> 适用范围：Vue 3 前端、FastAPI 后端、诗词领域模型、API 响应和功能增量开发

## 1. 文档目标

这份文档用于确定项目的前后端边界，避免在开发过程中出现以下问题：

1. 前端自己拼接口路径，后端修改路由后大量页面失效。
2. 每个接口返回不同结构，前端到处写特殊判断。
3. 页面路由权限被误当成真正的后端权限控制。
4. 爬取字段直接污染诗词主表，导致数据结构随网站变化。
5. 一开始设计过细，后续功能无法增量添加。
6. 业务 CRUD、爬取导入和 RAG 索引用三套互不相通的数据逻辑。

本阶段先建立框架和契约，不要求一次性实现所有接口。每个功能模块都按“数据库 -> 后端用例 -> API -> 前端 API 层 -> 页面 -> 测试”的顺序逐步落地。

### 1.1 文档地位与更新规则

1. 本文件保存接口的目标设计和稳定约定，是接口变更前的唯一设计入口。
2. FastAPI `/openapi.json` 保存代码当前实际生成的接口结构，是运行时事实源。
3. 本文件与 OpenAPI 不一致时属于缺陷，必须先修正文档或代码，再进行依赖该契约的联调。
4. 新增或修改接口时，先更新本文件中的方法和数据语义，再实现 Router、Schema、Service 和前端 API。
5. 接口完成后必须核对实际 OpenAPI，并在文末“接口变更记录”追加一条摘要。
6. 破坏性变化不能静默替换，必须提供兼容窗口、迁移方案或新的版本路径。
7. 未标注状态的历史条目表示目标设计，不自动代表当前已经实现；当前实现状态见 `PROJECT_GUIDE.md`。

---

## 2. 总体原则

### 2.1 前后端职责

| 层级 | 负责 | 不负责 |
| --- | --- | --- |
| Vue 页面 | 展示、交互、表单体验、路由跳转 | 最终权限判断、数据一致性 |
| Vue Store | 页面共享状态、会话状态、临时交互状态 | 替代后端业务规则 |
| 前端 API 层 | 统一请求、响应解析、错误提示、Token 刷新 | 查询数据库、拼接业务规则 |
| FastAPI Router | HTTP 协议、鉴权依赖、参数和响应映射 | 大量业务实现 |
| Service | 用例、事务边界、权限规则、跨模块编排 | 直接处理 Vue 页面状态 |
| Repository | MySQL、Redis、Qdrant 等数据访问 | 决定用户权限和业务状态 |
| Worker | 爬取、导入、切块、Embedding、索引 | 等待浏览器请求 |
| LangGraph | AI 问答状态和条件流程 | 普通诗词 CRUD |

### 2.2 核心约束

1. 所有前后端业务请求统一使用 `/api/v1` 前缀。
2. 页面路由和 API 路由分开定义，不能互相推导。
3. 后端响应使用统一 Envelope，SSE、文件下载和 `204` 响应例外。
4. API 字段统一使用 `snake_case`，前端 TypeScript 类型保持一致，避免隐式字段转换。
5. 时间统一返回 ISO 8601 UTC，例如 `2026-09-19T03:30:00Z`。
6. 列表接口统一分页参数和返回结构。
7. 错误使用稳定字符串错误码，前端不依赖中文消息做逻辑判断。
8. 前端路由守卫只改善体验，后端必须再次校验权限。
9. 爬取原始数据与规范化诗词数据分开保存。
10. 任何功能都按可验收的纵向切片增量加入。

---

## 3. 功能模块地图

### 3.1 P0 基础闭环

1. 用户注册、登录、刷新、退出和当前用户。
2. 管理员与普通用户角色。
3. 朝代、作者、分类和诗词基础数据。
4. 诗词公开查询、详情浏览。
5. 管理员诗词 CRUD 和发布状态。
6. 统一 API 响应、异常、分页和前端请求封装。
7. 第一条数据库迁移和自动化测试。

### 3.2 P1 数据导入与问答

1. 爬取源配置、爬取任务和原始记录。
2. 爬取结果清洗、去重、审核和发布。
3. 诗词 chunk 和 Qwen-Embedding 向量索引。
4. 用户会话、LangGraph 问答和 SSE 流式输出。
5. 引用、反馈和基础 RAG 评估。

### 3.3 P2 后续功能

1. 收藏、浏览历史、批注和个人诗集。
2. 高级全文检索、混合检索和重排。
3. 用户贡献、审核工作流和版本对比。
4. 评估面板、数据质量面板和运营指标。
5. 多模型路由和多 Agent 工作流。

---

## 4. 前端框架设计

### 4.1 技术基线

```text
Vue 3
TypeScript
Vite
Vue Router
Pinia
Naive UI 或 Element Plus
Vitest
Playwright
```

UI 组件库只能选一套作为主要设计系统。后续如需要特殊组件，优先扩展当前设计系统，不再引入第二套完整 UI 框架。

### 4.2 目录职责

```text
apps/web/src/
├─ api/
│  ├─ http.ts                 # Axios/fetch、拦截器、错误解析
│  ├─ auth.ts
│  ├─ poems.ts
│  ├─ authors.ts
│  ├─ categories.ts
│  ├─ chat.ts
│  └─ admin.ts
├─ components/                # 全局通用展示组件
├─ features/
│  ├─ auth/                   # 某功能的组件、类型、组合式函数
│  ├─ poems/
│  ├─ search/
│  ├─ chat/
│  └─ admin/
├─ layouts/
│  ├─ PublicLayout.vue
│  ├─ UserLayout.vue
│  └─ AdminLayout.vue
├─ router/
│  ├─ index.ts
│  ├─ guards.ts
│  └─ routes.ts
├─ stores/
│  ├─ auth.ts
│  ├─ ui.ts
│  └─ conversation.ts
├─ styles/
├─ types/
│  └─ api.generated.ts        # 由 OpenAPI 生成
├─ views/
└─ main.ts
```

原则：

1. 页面只负责组织功能和组合组件。
2. 可复用交互放到 `features`，不要先放进全局 `components`。
3. 所有 HTTP 请求必须经过 `api/http.ts`。
4. OpenAPI 生成的类型放在 `types/api.generated.ts`，不要手动修改。
5. Pinia 只保存需要跨页面共享或长期存在的状态。

### 4.3 页面路由表

| 路由名 | 路径 | 页面 | 访问规则 |
| --- | --- | --- | --- |
| `home` | `/` | 首页 | 公开 |
| `login` | `/login` | 登录 | 游客 |
| `register` | `/register` | 注册 | 游客 |
| `poem-list` | `/poems` | 诗词列表 | 公开 |
| `poem-detail` | `/poems/:poemId` | 诗词详情 | 公开，仅已发布 |
| `author-list` | `/authors` | 作者列表 | 公开 |
| `author-detail` | `/authors/:authorId` | 作者详情 | 公开 |
| `search` | `/search` | 综合搜索 | 公开 |
| `chat-home` | `/chat` | 问答首页 | 登录 |
| `chat-detail` | `/chat/:conversationId` | 会话详情 | 登录 |
| `profile` | `/me` | 个人中心 | 登录 |
| `favorites` | `/me/favorites` | 我的收藏 | 登录，P1 |
| `admin-home` | `/admin` | 管理概览 | 管理员 |
| `admin-poems` | `/admin/poems` | 诗词管理 | 管理员 |
| `admin-poem-create` | `/admin/poems/new` | 新建诗词 | 管理员 |
| `admin-poem-edit` | `/admin/poems/:poemId/edit` | 编辑诗词 | 管理员 |
| `admin-authors` | `/admin/authors` | 作者管理 | 管理员 |
| `admin-categories` | `/admin/categories` | 分类管理 | 管理员 |
| `admin-imports` | `/admin/imports` | 导入任务 | 管理员 |
| `admin-crawl-sources` | `/admin/crawl-sources` | 爬取源管理 | 管理员，P1 |
| `admin-users` | `/admin/users` | 用户管理 | 管理员，P1 |
| `forbidden` | `/403` | 无权限 | 公开 |
| `not-found` | `/:pathMatch(.*)*` | 404 | 公开 |

页面路由使用业务资源和小写连字符，不在页面路径中暴露后端详细结构。

### 4.4 Route Meta

```ts
declare module 'vue-router' {
  interface RouteMeta {
    title?: string
    requiresAuth?: boolean
    guestOnly?: boolean
    roles?: Array<'user' | 'admin'>
    layout?: 'public' | 'user' | 'admin'
    keepAlive?: boolean
  }
}
```

示例：

```ts
{
  path: 'poems/:poemId/edit',
  name: 'admin-poem-edit',
  component: () => import('@/views/admin/PoemEditView.vue'),
  meta: {
    title: '编辑诗词',
    requiresAuth: true,
    roles: ['admin'],
    layout: 'admin',
  },
}
```

### 4.5 路由守卫顺序

1. 设置页面标题。
2. 如果 `guestOnly` 且已经登录，跳转首页。
3. 如果 `requiresAuth` 且未登录，跳转登录页并保存 `redirect`。
4. 如果已有用户信息和 `roles` 不匹配，跳转 `/403`。
5. 页面加载后再由后端返回 `401/403` 做最终校验。

前端守卫不能代替后端鉴权。用户修改前端 Token 或 Store 不应获得任何管理员能力。

### 4.6 前端 API 层

所有模块统一使用 `request` 封装：

```ts
export interface ApiResponse<T> {
  success: boolean
  data: T | null
  meta: ApiMeta | null
  error: ApiError | null
  request_id: string
}

export interface ApiMeta {
  page?: number
  page_size?: number
  total?: number
  total_pages?: number
}

export interface ApiError {
  code: string
  message: string
  details?: ApiErrorDetail[]
}
```

请求层负责：

1. 自动添加 `Authorization: Bearer <access_token>`。
2. 为请求添加 `X-Request-ID`。
3. 解析统一响应 Envelope。
4. 对 `401` 执行一次 Token 刷新，并重放原请求。
5. 避免多个请求同时刷新 Token。
6. 将错误码转换为前端可处理异常。
7. 对 `204` 响应不尝试解析 JSON。
8. 对 SSE 使用独立 `fetch` 流解析，不使用普通 Axios 响应链。

---

## 5. 后端框架设计

### 5.1 FastAPI 目录职责

```text
apps/api/app/
├─ api/
│  ├─ deps.py                 # 当前用户、数据库、权限依赖
│  └─ v1/
│     ├─ router.py
│     ├─ auth.py
│     ├─ users.py
│     ├─ poems.py
│     ├─ authors.py
│     ├─ categories.py
│     ├─ search.py
│     ├─ conversations.py
│     └─ admin/
│        ├─ router.py
│        ├─ poems.py
│        ├─ authors.py
│        ├─ categories.py
│        ├─ users.py
│        ├─ imports.py
│        └─ crawl_sources.py
├─ core/
│  ├─ config.py
│  ├─ security.py
│  ├─ errors.py
│  ├─ logging.py
│  └─ response.py
├─ db/
│  ├─ base.py
│  ├─ session.py
│  └─ unit_of_work.py
├─ models/
├─ schemas/
├─ repositories/
├─ services/
├─ ai/
├─ workers/
└─ main.py
```

调用方向：

```text
Router -> Schema -> Service -> Repository -> Model / External Adapter
```

禁止：

1. Router 中直接写复杂 SQL。
2. Repository 内提交事务。
3. Service 返回 SQLAlchemy ORM 对象给 Router。
4. 前端直接依赖数据库字段而绕过 API Schema。
5. Worker 和 API 复制两套导入逻辑。

### 5.2 后端路由约定

1. 资源使用复数名词：`/poems`、`/authors`、`/categories`。
2. 公开读取和管理写操作分开。
3. 管理接口统一放在 `/api/v1/admin`。
4. 创建使用 `POST`，读取使用 `GET`，局部更新使用 `PATCH`，删除使用 `DELETE`。
5. 长任务提交使用 `POST` 并返回 `202 Accepted` 和 job ID。
6. 动作型接口只在确有必要时使用，例如 `/publish`、`/restore`、`/rebuild`。
7. 不把所有接口写进一个 `routes.py`。

### 5.3 API 路由清单

#### 认证

| 方法 | 路径 | 说明 | 权限 |
| --- | --- | --- | --- |
| POST | `/api/v1/auth/register` | 注册 | 公开 |
| POST | `/api/v1/auth/login` | 登录 | 公开 |
| POST | `/api/v1/auth/refresh` | 刷新 Token | Refresh Cookie |
| POST | `/api/v1/auth/logout` | 退出并撤销 Token | 登录 |
| GET | `/api/v1/users/me` | 当前用户 | 登录 |
| PATCH | `/api/v1/users/me` | 修改个人资料 | 登录 |
| POST | `/api/v1/users/me/password` | 修改密码 | 登录 |

#### 公开诗词库

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/v1/poems` | 已发布诗词分页列表 |
| GET | `/api/v1/poems/{poem_id}` | 已发布诗词详情 |
| GET | `/api/v1/authors` | 作者列表 |
| GET | `/api/v1/authors/{author_id}` | 作者详情及其作品 |
| GET | `/api/v1/dynasties` | 朝代列表 |
| GET | `/api/v1/categories` | 分类树或分类列表 |
| GET | `/api/v1/search` | 精确、标题、作者、分类搜索 |
| GET | `/api/v1/search/evidence` | 已发布作品的可解释 poem/line/note 证据检索 |

公开列表不接受 `status` 参数，后端固定查询已发布内容，避免前端越权读取草稿。
证据检索同样只读取已发布且未删除作品的当前版本，不接受调用方指定作品状态。

#### 用户问答

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| POST | `/api/v1/conversations` | 创建会话 |
| GET | `/api/v1/conversations` | 当前用户会话列表 |
| GET | `/api/v1/conversations/{id}` | 会话详情 |
| PATCH | `/api/v1/conversations/{id}` | 修改标题 |
| DELETE | `/api/v1/conversations/{id}` | 删除会话 |
| GET | `/api/v1/conversations/{id}/messages` | 消息列表 |
| POST | `/api/v1/conversations/{id}/messages:stream` | 发送消息并 SSE 输出 |
| POST | `/api/v1/messages/{message_id}/feedback` | 回答反馈 |

会话创建、列表、详情、标题修改、删除、消息列表和 SSE 问答已经实现；消息反馈仍属
目标设计。缺少有效 Chat Provider 时，`messages:stream` 在建立 SSE 前返回
`503 CHAT_MODEL_NOT_CONFIGURED`，且不写入用户消息或助手占位消息。

#### 管理端诗词 CRUD

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/v1/admin/poems` | 全部状态的诗词列表 |
| POST | `/api/v1/admin/poems` | 创建草稿 |
| GET | `/api/v1/admin/poems/{poem_id}` | 管理详情 |
| PATCH | `/api/v1/admin/poems/{poem_id}` | 局部更新 |
| DELETE | `/api/v1/admin/poems/{poem_id}` | 软删除 |
| POST | `/api/v1/admin/poems/{poem_id}/publish` | 发布 |
| POST | `/api/v1/admin/poems/{poem_id}/unpublish` | 取消发布 |
| POST | `/api/v1/admin/poems/{poem_id}/restore` | 恢复 |

作者、朝代和分类使用相同风格：

```text
/api/v1/admin/authors
/api/v1/admin/dynasties
/api/v1/admin/categories
```

#### 管理端导入与爬取

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/v1/admin/crawl-sources` | 爬取源列表 |
| POST | `/api/v1/admin/crawl-sources` | 新建爬取源 |
| PATCH | `/api/v1/admin/crawl-sources/{id}` | 更新规则和启用状态 |
| POST | `/api/v1/admin/crawl-jobs` | 创建爬取任务，返回 202 |
| GET | `/api/v1/admin/crawl-jobs/{id}` | 查看任务状态 |
| GET | `/api/v1/admin/crawl-jobs/{id}/items` | 查看抓取记录 |
| POST | `/api/v1/admin/crawl-items/{id}/approve` | 审核并转成草稿 |
| POST | `/api/v1/admin/import-jobs` | 上传文件导入 |
| GET | `/api/v1/admin/import-jobs/{id}` | 导入状态 |
| POST | `/api/v1/admin/indexes/rebuild` | 重建向量索引 |

#### 系统

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/v1/health/live` | 进程存活 |
| GET | `/api/v1/health/ready` | MySQL、Redis、Qdrant 等依赖就绪 |
| GET | `/api/v1/meta` | 版本、部署环境和功能开关 |

---

## 6. 诗词数据设计

### 6.1 设计目标

诗词数据同时面对三个来源：

1. 管理员手工 CRUD。
2. 后续网站爬取。
3. 文件批量导入。

它们不能各自写入不同结构。统一处理方式是：

```text
原始来源记录 -> 规范化 Draft -> 人工审核/自动校验 -> Published Poem -> 建立索引
```

### 6.2 核心实体

#### 朝代 `dynasties`

```text
id
name
code
start_year
end_year
sort_order
created_at
updated_at
```

朝代是相对稳定的基础实体，但仍然使用表而不是枚举，避免后续补充“五代十国”等情况时修改代码。

#### 作者 `authors`

```text
id
name
normalized_name
dynasty_id
aliases JSON
bio TEXT
avatar_url
status
created_at
updated_at
deleted_at
```

作者姓名可能存在异体字、别称和同名情况，因此保留 `normalized_name` 和 `aliases`。

#### 分类 `categories`

分类用于兼容爬取网站不同的划分方法，例如：

```text
诗
词
曲
五言绝句
七言绝句
豪放
婉约
思乡
送别
```

字段：

```text
id
name
code
category_type
parent_id
level
sort_order
source
status
created_at
updated_at
```

`category_type` 初始可使用：

```text
work_type    # 诗、词、曲
form         # 五言绝句、七言律诗
style        # 豪放、婉约
theme        # 思乡、送别
tag          # 来源网站自定义标签
```

分类不直接写死在 `poems` 表。分类与诗词通过 `poem_categories` 多对多关联：

```text
poem_id
category_id
source
is_primary
```

这样网站即使没有细分到“绝句”，只提供“诗”，系统也可以先保存较粗分类，后续再补充精细分类。

#### 诗词规范化主表 `poems`

```text
id
title
normalized_title
author_id
dynasty_id
content TEXT
normalized_content TEXT
summary TEXT
status
source_count
version_no
published_at
created_by
created_at
updated_at
deleted_at
```

说明：

1. `author_id` 和 `dynasty_id` 允许为空，因为部分来源可能缺少信息。
2. `content` 保存规范化后的正文。
3. `normalized_content` 用于去重、搜索和比对。
4. `status` 使用 `draft`、`published`、`archived`。
5. 分类、标签、来源等一对多或多对多关系不塞进 JSON 主字段。
6. 无法归一但暂时需要保留的扩展字段放入来源记录或明确的扩展 JSON，不放核心表。

#### 诗词来源 `poem_sources`

一个规范化作品可以来自人工录入、文件、网站或其他导入方式，因此来源记录与爬取配置解耦：

```text
id
poem_id
source_type
source_key
source_name
external_id
source_url
raw_title
raw_author_name
raw_dynasty_name
raw_content
raw_payload JSON
content_hash
license_note
fetched_at
created_at
updated_at
```

`source_type` 第一阶段使用 `manual`、`file`、`crawl`、`import`、`other`。`source_key` 用于稳定标识来源，例如 `manual`、`chinese-poetry` 或站点适配器名称。

约束：

```text
UNIQUE(source_key, external_id)
INDEX(content_hash)
```

原始内容必须保留来源和抓取时间，便于核对、去重和追溯。后续 `crawl_items` 审核通过后，通过导入 Service 写入本表，而不是让爬虫直接写诗词主表。

#### 结构化文件导入契约

当前已实现离线 CLI 导入，尚未实现上传文件、创建导入任务或查询任务状态的 HTTP API。后续爬虫应产出同一 JSON 结构并复用该 Service，不能直接写诗词主表。

命令：

```powershell
.\.venv\Scripts\python.exe apps\api\scripts\import_corpus.py `
  --input data\import\example_corpus_v1.json `
  --report data\import\reports\example-corpus-v1.json `
  --rebuild-chunks
```

支持参数：

| 参数 | 说明 |
| --- | --- |
| `--input` | 必填，UTF-8 JSON 数据集 |
| `--report` | 可选，写入完整 JSON 报告 |
| `--dry-run` | 只做 JSON 和 Schema 校验，不连接数据库 |
| `--rebuild-chunks` | 为本次 `created/updated` 版本重建 `structural-v1` chunks |

数据集最小结构：

```json
{
  "version": "dataset-v1",
  "source": {
    "source_key": "open-source-key",
    "source_type": "file",
    "source_name": "来源名称",
    "source_url": "https://example.com",
    "license_note": "许可说明"
  },
  "defaults": {
    "publish": false
  },
  "records": [
    {
      "external_id": "poem-1",
      "title": "静夜思",
      "content": "床前明月光……",
      "author_name": "李白",
      "dynasty_name": "唐",
      "categories": [
        {
          "name": "诗",
          "type": "work_type"
        }
      ],
      "tags": [
        "明月"
      ],
      "annotations": [],
      "raw_payload": {}
    }
  ]
}
```

导入语义：

1. 幂等键是 `source_key + external_id`，同一来源记录重复导入不会重复创建。
2. 结果状态为 `created`、`updated`、`unchanged`、`failed`。
3. 内容或受管元数据变化时递增 `poems.version_no`，创建 `change_type=import` 的不可变版本。
4. 默认导入为草稿；`publish=true` 可以发布，`publish=false` 不会撤回已发布作品。
5. 单条记录失败使用 savepoint 隔离，后续记录继续处理并写入报告。
6. `tags` 单项长度为 `1..80` 字符，单条最多 30 个，按 NFKC + casefold 去重。

首版没有导入批次表，也没有整批事务回滚。再次执行同一数据集可以幂等续跑。当前报告中的失败 `message` 可能包含底层异常字符串，未来暴露 HTTP API 前必须先分类和脱敏。

#### 诗词版本 `poem_versions`

手工编辑和自动导入都可能修改作品，因此需要不可变版本快照：

```text
id
poem_id
source_id
version_no
snapshot JSON
content_hash
change_type
changed_by_id
change_note
created_at
```

规则：

1. `snapshot` 至少包含标题、作者、朝代、正文、规范化正文、摘要、状态、分类和标签。
2. `UNIQUE(poem_id, version_no)`。
3. `source_id` 和 `changed_by_id` 删除时设为 `NULL`，保留历史快照。
4. `change_type` 第一阶段使用 `create`、`update`、`archive`、`restore`、`import`。
5. 版本创建后不修改。正文或关联字段变化时生成新版本；发布和撤回只影响索引可见状态，不改变作品内容版本号。
6. P0 记录版本快照，P1 再实现版本对比和回滚界面。

#### 诗词注释 `poem_annotations`

注释、译文、赏析、创作背景和典故必须关联到具体作品版本：

```text
id
poem_version_id
source_id
annotation_type
title
content
normalized_content
line_start
line_end
status
content_hash
created_at
updated_at
```

规则：

1. `annotation_type` 使用 `note`、`translation`、`appreciation`、`background`、`allusion`、`other`。
2. `line_start`、`line_end` 为空表示适用于整首作品；有值时从 1 开始计数。
3. 状态使用 `draft`、`published`、`archived`。
4. 删除作品版本时级联删除其注释。

#### 检索片段 `poem_chunks`

```text
id
poem_id
poem_version_id
annotation_id
granularity
chunk_index
text
normalized_text
content_hash
line_start
line_end
token_count
vector_id
embedding_model
embedding_dimension
chunk_strategy
status
created_at
updated_at
```

规则：

1. `granularity` 第一阶段使用 `poem`、`line`、`note`。
2. `chunk_index` 在相同版本、粒度和策略内从 0 开始。
3. `UNIQUE(poem_version_id, granularity, chunk_strategy, chunk_index)`。
4. `status` 使用 `pending`、`ready`、`failed`、`disabled`。
5. chunk 必须指向不可变版本；版本改变后重新生成，不原地改写旧 chunk。
6. Embedding 模型初步使用 `Qwen-Embedding`。实际模型 ID、向量维度、最大输入长度和批量限制在接入阶段验证后写入 ADR。
7. `structural-v1` 保留非空正文行的原始 1-based 行号；空行不重新编号。
8. `structural-v1` 只使用整篇、空行段落、标点和字符上限边界，不进行未经验证的韵脚或平仄识别。
9. 版本级重建只读取 `published` 注释；没有 `vector_id` 的旧 chunks 可以幂等替换。
10. 已存在 `vector_id` 时重建返回 `409 CHUNKS_ALREADY_INDEXED`，必须先清理向量索引。

#### 索引运行 `poem_index_runs`

一次切块、Embedding 和向量写入需要独立的运行记录，不能把运行状态混入 `poem_chunks`：

```text
id
poem_version_id
status
stage
chunk_strategy
embedding_model
embedding_dimension
vector_collection
config_snapshot JSON
chunk_count
embedded_count
error_message
created_by_id
started_at
finished_at
created_at
updated_at
```

生命周期：

```text
pending -> running -> succeeded
                   -> failed
pending/running -> failed
```

阶段：

```text
chunk -> embed -> upsert
```

规则：

1. `status` 使用 `pending`、`running`、`succeeded`、`failed`、`cancelled`；当前取消状态已建模，但取消流程尚未实现。
2. `stage` 使用 `chunk`、`embed`、`upsert`。
3. `poem_version_id` 删除时级联删除运行记录；`created_by_id` 删除时设为 `NULL`，保留审计记录。
4. 同一版本同一时间只允许一个 `pending` 或 `running` 运行；重复创建返回 `409 INDEX_RUN_ALREADY_ACTIVE`。
5. `config_snapshot` 只保存非敏感配置。键名包含 `api_key`、`authorization`、`cookie`、`password`、`secret` 或 `token` 时，值写入 `[REDACTED]`。
6. `embedding_dimension` 有值时必须大于 `0`；`embedded_count` 必须在 `0..chunk_count` 范围内。
7. `error_message` 最多保存 2000 字符，不保存供应商完整请求、响应头或凭据。
8. 当前仅实现 Service 和数据库记录，没有 HTTP 任务接口、Worker 租约、超时回收、自动重试或 active index 原子切换。

后续目标管理接口：

```text
POST /api/v1/admin/index-runs
GET  /api/v1/admin/index-runs/{id}
POST /api/v1/admin/index-runs/{id}/retry
POST /api/v1/admin/index-runs/{id}/cancel
```

创建任务应返回 `202 Accepted`，并支持 `Idempotency-Key`。这些接口尚未实现，不能按已完成能力联调。

### 6.3 问答相关表

#### 会话 `conversations`

```text
id
user_id
title
status
last_message_at
created_at
updated_at
```

规则：

1. `user_id` 指向用户，用户删除时级联删除会话。
2. `title` 长度为 `1..120`，去除首尾空白后不能为空。
3. 当前 `status` 默认为 `active`，为后续归档和软删除保留扩展空间。
4. 同一用户只能读取、修改和删除自己的会话；其他用户访问统一返回
   `404 CONVERSATION_NOT_FOUND`，避免泄露资源是否存在。
5. 列表按 `last_message_at`、`updated_at` 和 `id` 倒序返回，当前最多返回 50 条。

#### 消息 `messages`

```text
id
conversation_id
role
content
status
model
latency_ms
error_code
created_at
updated_at
```

规则：

1. `role` 使用 `user`、`assistant`。
2. `status` 使用 `streaming`、`completed`、`failed`、`cancelled`。
3. 用户消息在开始流式问答时以 `completed` 写入；助手消息先以 `streaming` 创建，
   成功后改为 `completed`，模型失败改为 `failed`，客户端取消改为 `cancelled`。
4. `model`、`latency_ms` 和 `error_code` 用于回答审计和失败排查，不包含供应商响应体、
   Token、Cookie 或密钥。
5. 历史上下文只读取 `completed` 且内容非空的消息，并由
   `CHAT_HISTORY_LIMIT` 限制最近轮数。

#### 消息引用 `message_citations`

```text
id
message_id
chunk_id
poem_id
poem_version_id
annotation_id
title
author_name
dynasty_name
granularity
text
score
rank
created_at
updated_at
```

规则：

1. `message_id` 删除时级联删除引用；`chunk_id` 删除时设为 `NULL`。
2. 引用保存作品、版本、注释、标题、作者、朝代、文本、分数和排名快照，历史回答不会因
   后续 chunk 重建或作品编辑而失去可读依据。
3. `chunk_id` 可回溯当前向量或文本 chunk，但历史展示不能只依赖当前 chunk 是否存在。
4. `rank` 从 1 开始，和一个助手消息内的引用顺序一致。
5. 只写入模型回答中实际引用的证据。候选证据中未被引用的部分不落库，也不发送
   `citation` 事件。

### 6.4 爬取相关表

#### 爬取源 `crawl_sources`

```text
id
name
base_url
adapter_key
config JSON
robots_policy JSON
rate_limit_per_minute
enabled
last_run_at
created_at
updated_at
```

#### 爬取任务 `crawl_jobs`

```text
id
source_id
status
total_items
success_items
failed_items
started_at
finished_at
error_message
created_by
created_at
```

#### 爬取原始项 `crawl_items`

```text
id
job_id
external_id
detail_url
raw_payload JSON
raw_html_key
parse_status
normalized_draft JSON
error_message
content_hash
created_at
updated_at
```

`raw_html_key` 指向 MinIO。数据库不保存大段 HTML，避免拖慢查询和备份。

### 6.5 爬取适配器边界

不同网站的 HTML 结构、分类和分页规则不能写进通用诗词 Service。建立适配器接口：

```python
class PoemSourceAdapter(Protocol):
    async def discover(self, cursor: str | None) -> DiscoveryResult: ...
    async def fetch_detail(self, url: str) -> RawSourceItem: ...
    def normalize(self, raw: RawSourceItem) -> PoemDraft: ...
```

在正式编写爬虫前必须确认：

1. 网站 `robots.txt`。
2. 服务条款和版权要求。
3. 请求频率、User-Agent 和并发限制。
4. 页面结构、分页方式、详情字段和唯一标识。
5. 分类是否稳定，还是由前端筛选参数动态生成。
6. 是否需要 OCR、JavaScript 渲染或登录。

爬虫只负责获取和初步解析。去重、人工审核、发布和索引统一调用导入 Service。

### 6.6 去重策略

按优先级执行：

1. 同一来源的 `source_key + external_id` 唯一。
2. 标题、作者和正文规范化哈希完全一致时判定为高概率重复。
3. 标题和作者接近、正文高度相似时标记为待审核。
4. 不同来源但版本不同，不直接覆盖，保留为不同 `poem_sources`。
5. 合并作品时记录人工决策，不静默删除来源。

### 6.7 CRUD 与爬取共用流程

```text
手工录入 --------------------┐
                            ├-> PoemDraft -> 校验 -> 保存/更新 -> 发布 -> 索引
文件导入 -> 解析 -----------┤
网站爬取 -> 适配器规范化 ---┘
```

保存和发布之间需要保留稳定版本边界：

```text
保存规范化作品
  -> 生成 poem_versions 快照
  -> 生成 poem / line / note chunks
  -> 发布
  -> 对指定版本执行 Embedding 和索引
```

这样可以保证：

1. 手工 CRUD 和爬取使用同一套字段校验。
2. 发布逻辑只有一份。
3. Embedding 和向量索引不会出现两套状态。
4. 后续增加导入源不需要改问答模块。
5. 已发布作品的引用可以回溯到具体版本、来源和行范围。

---

## 7. API 响应规范

### 7.1 成功响应 Envelope

```json
{
  "success": true,
  "data": {
    "id": 1001,
    "title": "静夜思",
    "author": {
      "id": 12,
      "name": "李白"
    },
    "dynasty": {
      "id": 3,
      "name": "唐"
    },
    "content": "床前明月光，疑是地上霜。举头望明月，低头思故乡。",
    "status": "published"
  },
  "meta": null,
  "error": null,
  "request_id": "req_01J..."
}
```

字段说明：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `success` | boolean | 仅表示业务处理是否成功 |
| `data` | object/array/null | 业务数据 |
| `meta` | object/null | 分页、游标或统计信息 |
| `error` | object/null | 成功时为 `null` |
| `request_id` | string | 用于日志和问题追踪 |

响应头同时返回：

```text
X-Request-ID: req_01J...
```

如果调用方传入合法 `X-Request-ID`，后端可以复用它；否则后端生成。

### 7.2 分页响应

```json
{
  "success": true,
  "data": [
    {
      "id": 1001,
      "title": "静夜思",
      "author_name": "李白",
      "dynasty_name": "唐",
      "categories": ["五言绝句"]
    }
  ],
  "meta": {
    "page": 1,
    "page_size": 20,
    "total": 135,
    "total_pages": 7
  },
  "error": null,
  "request_id": "req_01J..."
}
```

分页参数：

```text
page=1
page_size=20
sort=title
order=asc
```

约束：

1. `page >= 1`。
2. 默认 `page_size=20`。
3. 最大 `page_size=100`。
4. 排序字段使用白名单，不能直接拼 SQL。
5. 高吞吐日志或消息列表后续可使用游标分页，但响应仍使用统一结构。

### 7.3 错误响应

```json
{
  "success": false,
  "data": null,
  "meta": null,
  "error": {
    "code": "POEM_NOT_FOUND",
    "message": "诗词不存在或尚未发布",
    "details": []
  },
  "request_id": "req_01J..."
}
```

校验错误：

```json
{
  "success": false,
  "data": null,
  "meta": null,
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "请求参数校验失败",
    "details": [
      {
        "field": "title",
        "code": "missing",
        "message": "标题不能为空"
      }
    ]
  },
  "request_id": "req_01J..."
}
```

安全要求：

1. 错误详情不返回密码、Token、数据库语句和堆栈。
2. 生产环境 `message` 用于用户理解，调试信息写日志。
3. `code` 保持稳定，前端按 `code` 做逻辑处理。
4. 中文 `message` 后续可以国际化，但错误码不改变。

### 7.4 HTTP 状态码

| 状态码 | 用途 |
| --- | --- |
| 200 | 查询、更新成功 |
| 201 | 创建成功 |
| 202 | 已接受异步任务 |
| 204 | 删除成功，无响应体 |
| 400 | 请求语义错误 |
| 401 | 未登录或 Token 失效 |
| 403 | 已登录但无权限 |
| 404 | 资源不存在或对当前用户不可见 |
| 409 | 唯一键冲突、版本冲突或状态冲突 |
| 422 | 字段校验失败 |
| 429 | 请求过于频繁 |
| 500 | 未处理的服务端错误 |
| 503 | 依赖服务不可用 |

禁止所有错误都返回 HTTP `200` 再通过业务 `code` 表示失败。

### 7.5 `204` 与长任务

删除成功：

```http
HTTP/1.1 204 No Content
X-Request-ID: req_01J...
```

创建后台任务：

```json
{
  "success": true,
  "data": {
    "job_id": 88,
    "status": "queued"
  },
  "meta": null,
  "error": null,
  "request_id": "req_01J..."
}
```

HTTP 状态为 `202 Accepted`。前端通过任务查询接口或 WebSocket/SSE 跟进进度。

### 7.6 时间、空值和枚举

1. API 时间使用 UTC ISO 8601。
2. 可选字段明确返回 `null`，而不是随意省略。
3. 枚举值使用稳定英文：`draft`、`published`、`archived`。
4. 展示文字由前端词典或后端 `display_name` 提供，不能把中文当数据库状态值。

### 7.7 幂等与并发

1. 创建导入、爬取等可能重复提交的任务时支持 `Idempotency-Key`。
2. 管理端更新可携带 `version_no`。
3. 版本不一致时返回 `409 RESOURCE_VERSION_CONFLICT`。
4. 爬取来源使用数据库唯一约束保证幂等。

---

## 8. 用户与权限流程

### 8.1 角色

第一版角色：

```text
user
admin
```

权限原则：

1. 游客可以浏览公开诗词和作者。
2. `user` 可以问答、管理自己的会话和个人资料。
3. `admin` 可以管理诗词、作者、分类、导入任务和用户。
4. 资源访问必须校验所有权，不能只判断是否登录。

### 8.2 Token 方案

建议：

```text
Access Token: 短时有效，保存在前端内存
Refresh Token: HttpOnly + Secure + SameSite Cookie
```

流程：

1. 登录成功后 Access Token 返回前端内存。
2. Refresh Token 通过 Cookie 设置，不写入 JavaScript。
3. 页面刷新后调用 `/auth/refresh` 恢复登录态。
4. 退出时后端撤销 Refresh Token 并清理 Cookie。
5. 刷新接口并发时必须合并，避免多个请求重复刷新。
6. 开发环境通过 Vite Proxy 代理 `/api`，尽量保持同源。

如果部署后必须跨站发送 Cookie，需要增加 CSRF 防护和明确的 CORS 白名单。

### 8.3 后端鉴权依赖

```python
async def get_current_user(...) -> CurrentUser: ...


async def require_admin(
    user: CurrentUser = Depends(get_current_user),
) -> CurrentUser: ...
```

每个管理路由都显式使用 `require_admin`。不能只在父 Router 加一次权限后假设所有子路由都安全，也不要依赖前端路径判断。

---

## 9. 诗词 CRUD 契约

### 9.1 创建诗词

```http
POST /api/v1/admin/poems
Content-Type: application/json
Authorization: Bearer <access_token>
```

```json
{
  "title": "静夜思",
  "author_id": 12,
  "dynasty_id": 3,
  "content": "床前明月光，疑是地上霜。举头望明月，低头思故乡。",
  "summary": null,
  "category_ids": [21],
  "source": {
    "source_name": "人工录入",
    "source_url": null,
    "license_note": null
  }
}
```

创建后默认状态为 `draft`。只有 `publish` 后普通用户才能查询。

### 9.2 查询诗词列表

公开：

```http
GET /api/v1/poems?page=1&page_size=20&dynasty_id=3&author_id=12&category_id=21&q=月
```

管理：

```http
GET /api/v1/admin/poems?page=1&page_size=20&status=draft&q=静夜
```

筛选规则：

1. `q` 搜索标题、正文、作者和标签。
2. 多个筛选条件默认为 AND。
3. 同一字段的多值筛选后续使用重复参数或逗号分隔，需在 OpenAPI 中固定。
4. 不提供前端任意字段排序。

### 9.3 更新诗词

```http
PATCH /api/v1/admin/poems/1001
```

```json
{
  "title": "静夜思",
  "content": "床前明月光，疑是地上霜。",
  "version_no": 3
}
```

使用 `PATCH` 而不是 `PUT`，避免前端为修改一个字段提交完整对象。服务端校验 `version_no` 并生成版本快照。

### 9.4 删除诗词

```http
DELETE /api/v1/admin/poems/1001
```

默认软删除：

1. 公开查询立即不可见。
2. 向量索引异步撤销。
3. 管理员可以恢复。
4. 历史消息中的引用保留必要快照，避免历史回答出现空白。

### 9.5 分类管理

分类接口需要支持：

1. 新建和更新分类。
2. 设置父分类。
3. 调整排序。
4. 禁用分类但不删除历史关联。
5. 合并重复分类。
6. 返回分类树。

树形结构返回示例：

```json
{
  "id": 1,
  "name": "诗",
  "category_type": "work_type",
  "children": [
    {
      "id": 2,
      "name": "五言绝句",
      "category_type": "form",
      "children": []
    }
  ]
}
```

当分类规模增长后，分类树可以缓存到 Redis。分类变更后主动失效缓存。

### 9.6 可解释证据检索

```http
GET /api/v1/search/evidence?q=明月&limit=10&granularity=line&author_id=12
```

该接口用于 RAG 检索基线和后续引用，不返回自然语言答案。当前策略为
`lexical-baseline-v1`。

请求参数：

| 参数 | 类型 | 约束 | 说明 |
| --- | --- | --- | --- |
| `q` | string | 1 至 200 字符 | 查询文本；仅空白时返回 `422 VALIDATION_ERROR` |
| `limit` | int | 1 至 50，默认 10 | 最终返回的证据数 |
| `granularity` | string[] | `poem`、`line`、`note` | 可重复传入；省略表示全部粒度 |
| `author_id` | int | 可选 | 按作者过滤 |
| `dynasty_id` | int | 可选 | 按朝代过滤 |

`data` 为 `RetrievalEvidence[]`，每条证据包含：

1. 定位字段：`chunk_id`、`poem_id`、`poem_version_id`、`annotation_id`。
2. 展示字段：`title`、`author_id`、`author_name`、`dynasty_id`、`dynasty_name`、`text`。
3. 结构字段：`granularity`、`chunk_index`、`line_start`、`line_end`、`chunk_strategy`、`status`。
4. 解释字段：`score` 和 `match_types`。
5. 来源时间：`published_at`。

`meta` 包含 `strategy`、`query`、`normalized_query`、`candidate_count`、`limit`、
`granularity`、`author_id` 和 `dynasty_id`。

可见性和评分规则：

1. 只返回 `published` 且未软删除作品的当前版本 chunks。
2. 只允许 `pending`、`ready` chunks；note chunk 还必须关联 `published` 注释。
3. `chunk_exact=1.0`；`chunk_phrase=0.7 + 0.25 * 查询覆盖率`；标题、作者和朝代匹配分别为 `0.6`、`0.55`、`0.5`。
4. 同一证据取最高分，排序为得分降序、`line/poem/note` 优先级、`chunk_id` 升序。
5. `match_types` 是可解释基线，不代表最终相关性模型或准确率提升。
6. 接口不暴露 `vector_id`、Embedding 模型、Token 或数据库内部实现。

内部已经实现 `dense-baseline-v1` Service：先调用 Qwen Embedding 查询向量，
再从 Qdrant 召回候选，最后回查 MySQL 校验作品发布状态、当前版本、chunk 状态、
注释可见性和请求过滤条件。

内部还实现了 `hybrid-rrf-v1` Service：顺序调用词法与 Dense 分支，按
`1 / (60 + rank)` 融合排名，以 `chunk_id` 去重并归一化输出分数。两条内部策略
当前只供 Service 测试和离线评估脚本使用，尚未切换公开 HTTP，因此本节接口契约
仍以 `lexical-baseline-v1` 为准。

内部还实现了 `expanded-lexical-v1` Service：先由 `LexiconQueryRewriter` 识别
月亮、思乡、作者和朝代等有限领域概念，再将原查询与改写变体分别召回，使用
`1 / (60 + rank)` 做 RRF 融合，并以 `chunk_id` 去重。该策略保留原查询的精确匹配
能力，输出会附加 `query_expansion` 和 `rrf_fusion` 匹配标记。它当前同样只供内部
Service、测试和离线评估使用，不改变公开接口契约。

固定评估集 `lexical-baseline-seed-v1` 已在真实 MySQL 执行 Top-5 基线：27 条样本中
24 条通过，Recall@5 和 MRR 均为 `0.869565`，拒答准确率为 `1.0`。精确引用、短语、
标题、作者、朝代和多证据分类全部通过；3 条自然语言查询全部失败。内部 Dense Service
和 Hybrid RRF 已经可以接入同一评估集；Qdrant 适配器真实烟测已通过，Qwen 联调尚未完成。
`expanded-lexical-v1` 在同一 6 首种子语料和 27 条样本上达到 `27/27`、Recall@5
`1.0`、MRR `0.978261`，平均延迟从 `2.542 ms` 增至 `3.769 ms`；该结果不能外推为
开放语料或生产准确率。
后续 Sparse、Rerank 和公开策略切换必须使用同一组金标准证据与已有基线对比，
不能只报告提升项。

---

## 10. SSE 流式问答契约

### 10.1 请求

```http
POST /api/v1/conversations/88/messages:stream
Accept: text/event-stream
Content-Type: application/json
Authorization: Bearer <access_token>
```

```json
{
  "content": "李白诗歌中的月亮意象有哪些？",
  "model": null
}
```

前端使用 `fetch` 读取流，因为浏览器原生 `EventSource` 不支持自定义 `Authorization` Header，也不方便发送 POST JSON。

### 10.2 事件

```text
event: meta
data: {"message_id":301,"conversation_id":88}

event: retrieval
data: {"candidate_count":20,"selected_count":5,"strategy":"expanded-lexical-v1"}

event: delta
data: {"text":"从检索到的诗句看，"}

event: citation
data: {"chunk_id":501,"poem_id":1001,"poem_version_id":8,"annotation_id":null,"title":"静夜思","author_name":"李白","dynasty_name":"唐","granularity":"line","text":"床前明月光","score":0.91,"rank":1}

event: done
data: {"finish_reason":"stop","latency_ms":2380}

event: error
data: {"code":"MODEL_TIMEOUT","message":"模型响应超时，请稍后重试"}
```

规则：

1. 请求校验、会话权限和 Provider 配置检查在建立 SSE 前完成；失败时返回普通
   Envelope，常见为 `404 CONVERSATION_NOT_FOUND` 或
   `503 CHAT_MODEL_NOT_CONFIGURED`。
2. `meta` 在 SSE 建立后最先发送，返回已创建的助手消息 ID。
3. `retrieval` 在检索完成后发送，包含候选数、选中数和实际策略。
4. `delta` 只包含新增文本，不重复发送完整内容。
5. `citation` 可以多次发送；每条携带引用快照和从 1 开始的 `rank`。
6. `done` 必须最后发送，表示消息和引用已经提交 MySQL。
7. 流建立后的模型或服务错误通过 `error` 事件发送，HTTP 状态仍可能已经是 `200`；
   前端必须以事件为准，不能只看 HTTP 状态。
8. `error` 后不再发送 `delta`，助手消息保存为 `failed` 并记录 `error_code`。
9. 客户端断开时，后端停止无用生成，并尽力把助手消息保存为 `cancelled`。
10. SSE 不套普通 JSON Envelope，但错误事件中的 `code` 与普通 API 错误码保持一致。
11. 当前在线检索固定使用 `expanded-lexical-v1`，不读取公开 Dense、Hybrid 或 Rerank
    参数。
12. 检索结果为空时直接流式返回稳定拒答，不调用生成模型，也不创建引用记录。
13. 生成提示要求模型用 `[1]`、`[2]` 形式标注实际使用的证据；系统解析这些标记，只把
    被引用的证据写入 `citation` 事件和 `message_citations`。
14. 有证据但回答不含任何引用标记时，`error.code` 为 `CHAT_CITATION_MISSING`，助手
    消息保存为 `failed`，不写入引用。
15. 回答引用了不存在的编号时，`error.code` 为 `CHAT_CITATION_INVALID`，助手消息保存
    为 `failed`，不写入引用。

---

## 11. 错误码规范

错误码格式：

```text
<DOMAIN>_<REASON>
```

示例：

| 错误码 | HTTP | 说明 |
| --- | --- | --- |
| `AUTH_INVALID_CREDENTIALS` | 401 | 账号或密码错误 |
| `AUTH_TOKEN_EXPIRED` | 401 | Access Token 失效 |
| `AUTH_REFRESH_REVOKED` | 401 | Refresh Token 已撤销 |
| `AUTH_FORBIDDEN` | 403 | 无权限 |
| `USER_EMAIL_EXISTS` | 409 | 邮箱已注册 |
| `POEM_NOT_FOUND` | 404 | 诗词不存在或不可见 |
| `POEM_VERSION_CONFLICT` | 409 | 编辑版本冲突 |
| `POEM_INVALID_STATUS` | 409 | 当前状态不允许操作 |
| `POEM_VERSION_NOT_FOUND` | 404 | 诗词版本不存在 |
| `CHUNKS_ALREADY_INDEXED` | 409 | 该版本已有向量索引，需先清理 |
| `INDEX_RUN_NOT_FOUND` | 404 | 索引运行记录不存在 |
| `INDEX_RUN_INVALID_STATUS` | 409 | 索引运行状态不允许当前转换 |
| `INDEX_RUN_ALREADY_ACTIVE` | 409 | 同一版本已有待执行或执行中的索引运行 |
| `CATEGORY_HAS_CHILDREN` | 409 | 分类仍有子分类 |
| `CONVERSATION_NOT_FOUND` | 404 | 会话不存在或不属于当前用户 |
| `CRAWL_SOURCE_DISABLED` | 409 | 爬取源未启用 |
| `CRAWL_POLICY_REJECTED` | 403 | 不满足 robots 或站点策略 |
| `IMPORT_JOB_FAILED` | 409 | 导入任务失败 |
| `CHAT_CONTEXT_TOO_LONG` | 422 | 上下文超过限制 |
| `CHAT_MODEL_NOT_CONFIGURED` | 503 | Chat Provider 未配置，错误发生在 SSE 建立前 |
| `CHAT_EMPTY_RESPONSE` | SSE `error` | 模型返回空回答 |
| `CHAT_CITATION_MISSING` | SSE `error` | 有检索证据但回答没有任何 `[n]` 引用标记 |
| `CHAT_CITATION_INVALID` | SSE `error` | 回答引用了检索证据之外的编号 |
| `MODEL_TIMEOUT` | 503 | 模型超时 |
| `MODEL_PROVIDER_ERROR` | SSE `error` | 其他模型供应商错误 |
| `RATE_LIMITED` | 429 | 请求过于频繁 |
| `INTERNAL_ERROR` | 500 | 未处理错误 |

业务错误码不包含数据库错误、供应商原始错误和堆栈信息。

---

## 12. OpenAPI 与类型同步

FastAPI 的 OpenAPI 是后端实现完成后的运行时契约来源；目标接口设计以本文档第 1.1 节
定义的规则为准。

流程：

```text
FastAPI Schema
  -> /openapi.json
  -> openapi-typescript
  -> apps/web/src/types/api.generated.ts
  -> 前端 API 模块复用生成类型
```

要求：

1. 每个 Router 增加清晰的 `summary`、`description` 和响应模型。
2. 公共错误响应在 OpenAPI 中声明。
3. CI 检查 OpenAPI 是否可生成以及前端类型是否同步。
4. 手写类型只用于纯前端状态，不复制 API DTO。
5. 接口破坏性变更使用新版本或明确迁移方案。

---

## 13. 测试策略

### 13.1 后端

1. Pydantic Schema 单元测试。
2. Service 业务规则测试。
3. Repository 使用真实 MySQL 容器测试。
4. API 状态码、响应 Envelope、权限和分页测试。
5. 爬取适配器使用固定 HTML Fixture，不访问真实网站。
6. ID 唯一性、去重、软删除和发布状态测试。
7. SSE 事件顺序和取消测试。

### 13.2 前端

1. API 层正确解析成功与错误响应。
2. Token 刷新只执行一次并正确重放请求。
3. 路由守卫验证登录、角色和重定向。
4. 表单校验与服务端错误字段映射。
5. 诗词列表、详情、CRUD 和引用展示组件测试。
6. Playwright 覆盖登录、创建诗词、发布、查询、问答主路径。

### 13.3 契约测试

1. 后端响应必须符合对应 Pydantic Schema。
2. 前端类型由同一 OpenAPI 生成。
3. 错误码表有唯一性测试。
4. 删除 `204`、异步 `202` 和 SSE 不被错误当作普通 JSON。
5. 分页元数据在列表接口中保持一致。

---

## 14. 增量开发路线

### 切片 0：工程与响应基线

后端：

1. FastAPI、Settings、日志、Request ID。
2. 统一响应 Envelope 和全局异常处理。
3. `/health/live`、`/health/ready`。
4. MySQL、Redis、Qdrant、MinIO 的 Docker Compose。

前端：

1. Vue、Router、Pinia、UI 框架。
2. 基础 Layout、404、403。
3. API 请求封装和错误展示。

验收：前后端通过统一响应完成一次健康状态展示。

### 切片 1：用户系统

后端：

1. 用户表、Refresh Token 表。
2. 注册、登录、刷新、退出、当前用户。
3. `user/admin` 权限依赖。

前端：

1. 登录、注册页。
2. Auth Store 和路由守卫。
3. Token 刷新和退出。

验收：普通用户和管理员能看到不同的导航和页面权限，后端拒绝越权请求。

### 切片 2：诗词基础库

后端：

1. 朝代、作者、分类、诗词和关联表。
2. 公开查询和详情。
3. 管理员诗词 CRUD、发布、取消发布、软删除。
4. 第一条完整导入迁移和种子数据。

前端：

1. 诗词列表、详情和作者页。
2. 管理端诗词列表、创建、编辑和发布。

验收：管理员创建一首草稿并发布，游客可以查看，未发布内容不可见。

### 切片 3：分类和爬取来源

后端：

1. 分类树和分类合并。
2. 爬取源、任务、原始项和审核。
3. 第一个网站 Adapter，但必须先核对站点规范。

前端：

1. 分类管理。
2. 爬取源配置和任务进度。
3. 抓取结果审核和转草稿。

验收：固定 Fixture 能稳定解析，真实站点抓取遵守频率与规则，重复运行不产生重复数据。

### 切片 4：RAG 索引和问答

后端：

1. 使用 `Qwen-Embedding` 生成向量。
2. Qdrant 索引、重建和对账。
3. 将 Chat Provider 从当前默认的 `deepseek-chat` 调整为经真实账号验证后的目标模型。
4. LangGraph、SSE、引用和反馈。

前端：

1. 会话列表、流式回答、引用卡片和反馈。

验收：已发布诗词可被检索，答案只基于引用内容，模型失败时前端能优雅提示。

### 切片 5：质量与部署

1. RAG 固定评估集和回归指标。
2. 性能、限流、日志和监控。
3. E2E、依赖检查和部署文档。

---

## 15. 模型基线与待确认项

### 15.1 初步模型

| 用途 | 初步选择 | 状态 |
| --- | --- | --- |
| Embedding | Qwen-Embedding | 初步确定 |
| 问答生成 | DeepSeek 4.1 Flash | 方向确定；精确模型 ID 待确认，代码默认 `deepseek-chat` |

阶段 0 需要确认：

1. 供应商和完整模型 ID。
2. API Base URL、鉴权方式和 OpenAI 兼容程度。
3. 最大上下文、最大输出、流式协议和 Function Calling 能力。
4. Embedding 维度、批量大小、限流和计费。
5. 是否支持结构化输出，以及不支持时的替代方案。
6. 数据发送到第三方的隐私与合规要求。
7. 模型不可用时的降级策略。

模型必须通过 Provider Adapter 调用。业务 Service 和 LangGraph 不直接依赖某个 SDK 的私有对象。

当前已实现 `ChatModelPort` 和 DeepSeek OpenAI-compatible 流式 Chat Provider：

```text
POST {DEEPSEEK_BASE_URL}/chat/completions
Authorization: Bearer <DEEPSEEK_API_KEY>
```

配置项：

| 环境变量 | 说明 |
| --- | --- |
| `DEEPSEEK_API_KEY` | 未配置时聊天接口返回 `503 CHAT_MODEL_NOT_CONFIGURED` |
| `DEEPSEEK_BASE_URL` | 默认 `https://api.deepseek.com` |
| `DEEPSEEK_CHAT_MODEL` | 默认 `deepseek-chat`，精确模型 ID 需按账号可用模型确认 |
| `DEEPSEEK_TIMEOUT_SECONDS` | 单次流式请求超时，默认 `60` 秒 |
| `DEEPSEEK_MAX_OUTPUT_TOKENS` | 单次回答最大输出 Token，默认 `1200`，上限 `8192` |
| `CHAT_RETRIEVAL_LIMIT` | 在线问答注入的证据数，默认 `5`，上限 `20` |
| `CHAT_HISTORY_LIMIT` | 注入的最近完成消息数，默认 `8`，上限 `50` |

行为约束：

1. Provider 只接受统一 `ChatMessage`，不向 Service 暴露供应商 SDK 类型。
2. 流式响应只解析 OpenAI-compatible `choices[0].delta.content` 和 `[DONE]`。
3. 超时映射为 `MODEL_TIMEOUT`；空回答映射为 `CHAT_EMPTY_RESPONSE`；其他供应商错误
   映射为 `MODEL_PROVIDER_ERROR`。
4. 模型响应错误只保存受控错误码；流式错误事件中的消息不包含上游完整响应体或密钥。
5. 在线问答先经过 `expanded-lexical-v1` 检索，再把证据构造为上下文，生成后必须有引用。
6. 检索结果为空时使用固定拒答文案，不调用生成模型。
7. DeepSeek Provider 已通过 Fake/HTTP 流测试，尚未使用真实账号完成端到端生成验证。
8. “DeepSeek 4.1 Flash”目前只是目标方向；当前默认模型 ID 是 `deepseek-chat`，
   在没有完成真实账号联调前，不能把两者描述为已经验证或已经接通。

当前已实现 `EmbeddingProvider` 协议和 Qwen Embedding 的 OpenAI-compatible 适配器：

```text
POST {DASHSCOPE_BASE_URL}/embeddings
Authorization: Bearer <DASHSCOPE_API_KEY>
```

配置项：

| 环境变量 | 说明 |
| --- | --- |
| `QWEN_EMBEDDING_MODEL` | 模型 ID，默认 `text-embedding-v4`，真实可用性待账号验证 |
| `QWEN_EMBEDDING_DIMENSION` | 可留空；写入索引前必须锁定真实维度 |
| `QWEN_EMBEDDING_BATCH_SIZE` | 单次请求最大文本数，默认 `10` |
| `QWEN_EMBEDDING_TIMEOUT_SECONDS` | 单次请求超时，默认 `30` |
| `QWEN_EMBEDDING_MAX_RETRIES` | 可重试错误的最大额外请求次数，默认 `2` |
| `QWEN_EMBEDDING_RETRY_BACKOFF_SECONDS` | 指数退避基数，默认 `0.5` |

行为约束：

1. 文档批量按配置拆分，响应按 `index` 重排并校验数量。
2. 网络错误、`408`、`429` 和 `5xx` 可有限重试；`401/403/422` 等直接失败。
3. 空向量、索引无效、数量不符和维度不一致均抛出 Provider 错误。
4. API Key 不得写入日志、`poem_index_runs.config_snapshot` 或错误报告。
5. 当前已实现 chunks -> Qwen Embedding -> Qdrant upsert 的最小索引闭环，并回写 `vector_id`、模型和维度。
6. 当前已实现 `dense-baseline-v1` 内部检索 Service，支持 Qdrant 查询、元数据过滤和 MySQL 可见性回查；公开 HTTP 尚未切换。
7. 当前仍未实现旧向量清理、active index 切换和跨库对账；Qdrant 已完成真实适配器烟测，DashScope 尚未联调。

### 15.2 数据待确认项

1. 目标网站及其 robots、条款和版权要求。
2. 网站分类层级是否稳定。
3. 作者和朝代字段是否完整。
4. 同一作品是否存在多个版本。
5. 繁体、简体、异体字如何保留和规范化。
6. 注释、译文、赏析是否属于题库范围。
7. 原始 HTML 的保存周期和清理策略。

在抓取规范确认前，不编写针对具体网站的解析器。

---

## 16. 当前阶段的实现顺序

建议按以下顺序开始编码：

1. 初始化 Git、`apps/web`、`apps/api`、`infra`、`docs`。
2. 建立 Docker Compose 和 `.env.example`。
3. 实现 FastAPI 配置、日志、Request ID、响应 Envelope 和健康检查。
4. 初始化 Vue、Router、Pinia、Layout、404/403 和 API 请求层。
5. 建立第一条 Alembic 迁移，先创建用户表。
6. 实现用户注册、登录、Token 刷新、`/users/me`。
7. 前端完成登录、退出和路由守卫。
8. 再进入作者、朝代、分类和诗词 CRUD。

每一步完成后都在 `DEVELOPMENT_LOG.md` 增加记录，并更新本文档中的接口变化。

---

## 17. 接口变更记录

| 日期 | 变更 | 兼容性 | 验证 |
| --- | --- | --- | --- |
| 2026-09-19 | 建立前后端路由、响应、错误码和增量切片契约 | 初始基线 | 文档审阅 |
| 2026-09-19 | 补充契约唯一事实源、OpenAPI 对账和变更记录规则 | 非破坏性 | 文档链接与规则检查 |
| 2026-09-19 | 补充来源、不可变版本、注释和多粒度 chunk 数据契约 | 新增表，不修改现有接口 | 迁移、模型和 CRUD 回归测试 |
| 2026-09-19 | 新增 `poem_index_runs` 索引运行元数据、生命周期、并发和脱敏契约 | 新增表，不修改现有接口；HTTP 任务接口仍属目标设计 | `33 passed`、Ruff 通过、真实 MySQL `0004 (head)` |
| 2026-09-19 | 新增 `GET /api/v1/search/evidence` 可解释词法证据检索契约 | 新增公开只读接口，不修改现有搜索接口 | `36 passed`、Ruff 通过、真实 MySQL 服务烟测和 OpenAPI 对账 |
| 2026-09-19 | 固定 `lexical-baseline-seed-v1` 检索评估集和可移植金标准选择器 | 新增离线评估脚本，不修改 HTTP 接口 | `38 passed`、Ruff 通过、真实 MySQL Top-5 基线 `Recall@5=0.869565`、`MRR=0.869565` |
| 2026-09-19 | 新增结构化文件导入 CLI、JSON 契约、幂等更新和逐条失败报告 | 新增离线导入能力，不修改 HTTP 接口；任务化导入仍属目标设计 | `44 passed`、Ruff 通过、CLI `--help` 和示例 `--dry-run` 通过 |
| 2026-09-19 | 新增 `EmbeddingProvider` 和 Qwen Embedding OpenAI-compatible 适配器 | 新增内部 Provider，不修改 HTTP 接口；Qdrant 与 chunks 写入仍属目标设计 | Provider `8 passed`、Ruff 通过、mypy 通过 |
| 2026-09-19 | 新增 Qdrant 向量存储端口、`dense` named vector、chunk 映射回写和最小索引闭环 | 新增内部索引能力，不修改 HTTP 接口；检索与任务化仍属目标设计 | `65 passed`、Ruff 通过、mypy 通过；真实 Qdrant 未启动 |
| 2026-09-19 | 新增 `dense-baseline-v1` 内部 Dense 检索 Service、Qdrant 查询端口和 MySQL 可见性回查 | 新增内部检索能力和离线评估策略，不修改公开 HTTP 接口 | `72 passed`、Ruff 通过、mypy 通过；真实 Qdrant 与 DashScope 未联调 |
| 2026-09-19 | 新增 `hybrid-rrf-v1` 内部 Hybrid RRF Service 和离线评估策略 | 新增内部融合能力，不修改公开 HTTP 接口 | Hybrid `5 passed`；真实 Qdrant 与 DashScope 未联调 |
| 2026-09-19 | 新增 `expanded-lexical-v1` 查询改写与多查询 RRF Service、版本化词典和离线评估策略 | 新增内部查询理解能力，不修改公开 HTTP 接口 | `85 passed`、Ruff 和 mypy 通过；真实 MySQL 种子集 `27/27`、Recall@5 `1.0`、MRR `0.978261` |
| 2026-09-19 | 新增可重复执行的 Qdrant 与 Qwen 烟测脚本，并完成真实 Qdrant `1.19.1` 适配器联调 | 不修改 HTTP 接口；Qwen 真实网络调用仍受环境审批阻塞 | `85 passed`、Ruff 和相关模块 mypy 通过；Qdrant 临时 Collection 创建、维度拒绝、upsert、过滤检索和删除通过 |
| 2026-09-20 | 新增会话、消息、引用快照、DeepSeek Chat Provider、四节点 LangGraph 问答和 SSE 流式接口 | 新增登录用户 API 和数据库表；消息反馈仍属目标设计 | `91 passed`、Ruff、前端 `9 passed`、类型检查和生产构建通过；真实 MySQL 迁移至 `0005`，真实 HTTP/SSE 无证据链路和消息持久化通过 |
