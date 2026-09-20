export type UserRole = 'user' | 'admin'
export type UserStatus = 'active' | 'disabled'
export type PoemStatus = 'draft' | 'published' | 'archived'
export type CategoryType = 'work_type' | 'form' | 'style' | 'theme'

export interface ApiErrorDetail {
  field?: string
  code: string
  message: string
  service?: string
  status?: string
}

export interface ApiError {
  code: string
  message: string
  details: ApiErrorDetail[]
}

export interface ApiMeta {
  page?: number
  page_size?: number
  total?: number
  total_pages?: number
}

export interface PaginationMeta {
  page: number
  page_size: number
  total: number
  total_pages: number
}

export interface Paginated<T> {
  items: T[]
  meta: PaginationMeta
}

export interface ApiResponse<T> {
  success: boolean
  data: T | null
  meta: ApiMeta | null
  error: ApiError | null
  request_id: string
}

export interface User {
  id: number
  email: string
  display_name: string
  role: UserRole
  status: UserStatus
  created_at: string
  updated_at: string
}

export interface AuthSession {
  access_token: string
  token_type: 'bearer'
  expires_in: number
  user: User
}

export interface PoemCategory {
  id: number
  name: string
  type: CategoryType
  parent_id: number | null
  sort_order: number
  is_active: boolean
  poem_count: number
  created_at: string
  updated_at: string
}

export interface PoemTag {
  id: number
  name: string
}

export interface Poem {
  id: number
  title: string
  author_id: number | null
  author_name: string | null
  dynasty_id: number | null
  dynasty_name: string | null
  content: string
  summary: string | null
  status: PoemStatus
  version_no: number
  published_at: string | null
  deleted_at: string | null
  categories: PoemCategory[]
  tags: PoemTag[]
  created_at: string
  updated_at: string
}

export interface PoemSummary {
  id: number
  title: string
  summary: string | null
  status: PoemStatus
  published_at: string | null
}

export interface Author {
  id: number
  name: string
  aliases: string[]
  bio: string | null
  dynasty_id: number | null
  dynasty_name: string | null
  poem_count: number
  created_at: string
  updated_at: string
}

export interface AuthorDetail extends Author {
  poems: PoemSummary[]
}

export interface Dynasty {
  id: number
  name: string
  description: string | null
  sort_order: number
  created_at: string
  updated_at: string
}

export type MessageRole = 'user' | 'assistant'
export type MessageStatus = 'streaming' | 'completed' | 'failed' | 'cancelled'

export interface Conversation {
  id: number
  title: string
  status: string
  last_message_at: string | null
  created_at: string
  updated_at: string
}

export interface MessageCitation {
  id: number
  chunk_id: number | null
  poem_id: number | null
  poem_version_id: number | null
  annotation_id: number | null
  title: string
  author_name: string | null
  dynasty_name: string | null
  granularity: string
  text: string
  score: number
  rank: number
}

export interface ChatMessage {
  id: number
  conversation_id: number
  role: MessageRole
  content: string
  status: MessageStatus
  model: string | null
  latency_ms: number | null
  error_code: string | null
  citations: MessageCitation[]
  created_at: string
  updated_at: string
}
