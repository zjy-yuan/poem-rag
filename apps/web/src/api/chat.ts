import { ApiClientError, requestData, requestNoContent } from './http'
import { getAccessToken, refreshAccessToken } from './session'
import { SseStreamParser } from '@/features/chat/sse'
import type {
  ApiError,
  ApiResponse,
  ChatMessage,
  Conversation,
  MessageCitation,
} from '@/types/api'

export interface ConversationCreatePayload {
  title?: string
}

export interface ConversationUpdatePayload {
  title: string
}

export interface ChatMetaEvent {
  message_id: number
  conversation_id: number
}

export interface ChatRetrievalEvent {
  candidate_count: number
  selected_count: number
  strategy: string
}

export interface ChatDeltaEvent {
  text: string
}

export interface ChatDoneEvent {
  finish_reason: string
  latency_ms: number
}

export interface ChatStreamErrorEvent {
  code: string
  message: string
}

export interface ChatStreamHandlers {
  onMeta?: (event: ChatMetaEvent) => void
  onRetrieval?: (event: ChatRetrievalEvent) => void
  onDelta?: (event: ChatDeltaEvent) => void
  onCitation?: (event: Omit<MessageCitation, 'id'>) => void
  onDone?: (event: ChatDoneEvent) => void
  onError?: (event: ChatStreamErrorEvent) => void
}

const apiBaseUrl = (import.meta.env.VITE_API_BASE_URL ?? '/api/v1').replace(/\/+$/, '')

export const chatApi = {
  listConversations(): Promise<Conversation[]> {
    return requestData<Conversation[]>({
      method: 'GET',
      url: '/conversations',
    })
  },

  createConversation(payload: ConversationCreatePayload = {}): Promise<Conversation> {
    return requestData<Conversation>({
      method: 'POST',
      url: '/conversations',
      data: payload,
    })
  },

  updateConversation(
    conversationId: number,
    payload: ConversationUpdatePayload,
  ): Promise<Conversation> {
    return requestData<Conversation>({
      method: 'PATCH',
      url: `/conversations/${conversationId}`,
      data: payload,
    })
  },

  deleteConversation(conversationId: number): Promise<void> {
    return requestNoContent({
      method: 'DELETE',
      url: `/conversations/${conversationId}`,
    })
  },

  listMessages(conversationId: number): Promise<ChatMessage[]> {
    return requestData<ChatMessage[]>({
      method: 'GET',
      url: `/conversations/${conversationId}/messages`,
    })
  },

  streamMessage(
    conversationId: number,
    content: string,
    handlers: ChatStreamHandlers,
    signal: AbortSignal,
  ): Promise<void> {
    return streamConversationMessage(conversationId, content, handlers, signal)
  },
}

async function streamConversationMessage(
  conversationId: number,
  content: string,
  handlers: ChatStreamHandlers,
  signal: AbortSignal,
): Promise<void> {
  const body = JSON.stringify({ content })
  let response = await openStream(conversationId, body, signal)

  if (response.status === 401) {
    const token = await refreshAccessToken()
    if (token) {
      response = await openStream(conversationId, body, signal, token)
    }
  }

  if (!response.ok) {
    throw await responseError(response)
  }
  if (!response.body) {
    throw new ApiClientError({
      code: 'CHAT_STREAM_UNAVAILABLE',
      message: '当前浏览器无法读取问答流',
      details: [],
    })
  }

  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  const parser = new SseStreamParser()
  let terminalEventReceived = false

  const dispatch = (event: { event: string; data: string }): void => {
    const data = parseEventData(event.data)
    switch (event.event) {
      case 'meta':
        handlers.onMeta?.(data as ChatMetaEvent)
        break
      case 'retrieval':
        handlers.onRetrieval?.(data as ChatRetrievalEvent)
        break
      case 'delta':
        handlers.onDelta?.(data as ChatDeltaEvent)
        break
      case 'citation':
        handlers.onCitation?.(data as Omit<MessageCitation, 'id'>)
        break
      case 'done':
        terminalEventReceived = true
        handlers.onDone?.(data as ChatDoneEvent)
        break
      case 'error':
        terminalEventReceived = true
        handlers.onError?.(data as ChatStreamErrorEvent)
        break
    }
  }

  while (true) {
    const { done, value } = await reader.read()
    if (done) {
      break
    }
    for (const event of parser.push(decoder.decode(value, { stream: true }))) {
      dispatch(event)
    }
  }

  const remaining = decoder.decode()
  if (remaining) {
    for (const event of parser.push(remaining)) {
      dispatch(event)
    }
  }
  for (const event of parser.finish()) {
    dispatch(event)
  }

  if (!terminalEventReceived && !signal.aborted) {
    throw new ApiClientError({
      code: 'CHAT_STREAM_INTERRUPTED',
      message: '问答连接意外中断，请重试',
      details: [],
    })
  }
}

async function openStream(
  conversationId: number,
  body: string,
  signal: AbortSignal,
  accessToken?: string,
): Promise<Response> {
  const headers = new Headers({
    Accept: 'text/event-stream',
    'Content-Type': 'application/json',
  })
  const token = accessToken ?? getAccessToken()
  if (token) {
    headers.set('Authorization', `Bearer ${token}`)
  }
  return fetch(`${apiBaseUrl}/conversations/${conversationId}/messages:stream`, {
    method: 'POST',
    headers,
    body,
    credentials: 'include',
    signal,
  })
}

async function responseError(response: Response): Promise<ApiClientError> {
  let payload: ApiResponse<unknown> | null = null
  try {
    payload = (await response.json()) as ApiResponse<unknown>
  } catch {
    payload = null
  }
  const error: ApiError = payload?.error ?? {
    code: 'CHAT_REQUEST_FAILED',
    message: '问答请求失败，请稍后重试',
    details: [],
  }
  return new ApiClientError(error, response.status)
}

function parseEventData(data: string): unknown {
  try {
    return JSON.parse(data) as unknown
  } catch {
    throw new ApiClientError({
      code: 'CHAT_STREAM_INVALID_EVENT',
      message: '问答流返回了无法识别的数据',
      details: [],
    })
  }
}
