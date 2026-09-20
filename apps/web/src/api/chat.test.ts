import { afterEach, describe, expect, it, vi } from 'vitest'

import { chatApi, type ChatStreamHandlers } from './chat'
import { setAccessToken, setRefreshHandler } from './session'

const doneBody = [
  'event: meta',
  'data: {"message_id":11,"conversation_id":7}',
  '',
  'event: retrieval',
  'data: {"candidate_count":3,"selected_count":1,"strategy":"expanded-lexical-v1"}',
  '',
  'event: delta',
  'data: {"text":"明月常与思乡相连。"}',
  '',
  'event: done',
  'data: {"finish_reason":"stop","latency_ms":42}',
  '',
].join('\n')

const errorBody = [
  'event: meta',
  'data: {"message_id":12,"conversation_id":7}',
  '',
  'event: error',
  'data: {"code":"MODEL_TIMEOUT","message":"模型响应超时，请稍后重试"}',
  '',
].join('\n')

function sseResponse(body: string): Response {
  return new Response(body, {
    status: 200,
    headers: { 'Content-Type': 'text/event-stream' },
  })
}

function fetchInit(fetchMock: ReturnType<typeof vi.fn>, index: number): RequestInit {
  const call = fetchMock.mock.calls[index] as [string, RequestInit] | undefined
  if (!call) {
    throw new Error(`missing fetch call at index ${index}`)
  }
  return call[1]
}

afterEach(() => {
  setAccessToken(null)
  setRefreshHandler(async () => null)
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

describe('chatApi.streamMessage', () => {
  it('refreshes once after 401 and retries with the new access token', async () => {
    setAccessToken('expired-token')
    const refresh = vi.fn(async () => 'fresh-token')
    setRefreshHandler(refresh)

    const fetchMock = vi.fn()
    fetchMock
      .mockResolvedValueOnce(new Response(null, { status: 401 }))
      .mockResolvedValueOnce(sseResponse(doneBody))
    vi.stubGlobal('fetch', fetchMock)

    const handlers: ChatStreamHandlers = {
      onDelta: vi.fn(),
      onDone: vi.fn(),
    }

    await chatApi.streamMessage(
      7,
      '静夜思里的月亮有什么含义？',
      handlers,
      new AbortController().signal,
    )

    expect(refresh).toHaveBeenCalledTimes(1)
    expect(fetchMock).toHaveBeenCalledTimes(2)
    expect(
      (fetchInit(fetchMock, 0).headers as Headers).get('Authorization'),
    ).toBe('Bearer expired-token')
    expect(
      (fetchInit(fetchMock, 1).headers as Headers).get('Authorization'),
    ).toBe('Bearer fresh-token')
    expect(handlers.onDelta).toHaveBeenCalledWith({ text: '明月常与思乡相连。' })
    expect(handlers.onDone).toHaveBeenCalledWith({
      finish_reason: 'stop',
      latency_ms: 42,
    })
  })

  it('treats an error event as a terminal stream result', async () => {
    const fetchMock = vi.fn().mockResolvedValue(sseResponse(errorBody))
    vi.stubGlobal('fetch', fetchMock)

    const handlers: ChatStreamHandlers = {
      onError: vi.fn(),
      onDone: vi.fn(),
    }

    await expect(
      chatApi.streamMessage(
        7,
        '触发模型错误',
        handlers,
        new AbortController().signal,
      ),
    ).resolves.toBeUndefined()

    expect(handlers.onError).toHaveBeenCalledWith({
      code: 'MODEL_TIMEOUT',
      message: '模型响应超时，请稍后重试',
    })
    expect(handlers.onDone).not.toHaveBeenCalled()
  })

  it('rejects when the stream closes without done or error', async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      sseResponse('event: meta\ndata: {"message_id":13,"conversation_id":7}\n\n'),
    )
    vi.stubGlobal('fetch', fetchMock)

    await expect(
      chatApi.streamMessage(
        7,
        '模拟连接中断',
        {},
        new AbortController().signal,
      ),
    ).rejects.toMatchObject({
      code: 'CHAT_STREAM_INTERRUPTED',
      message: '问答连接意外中断，请重试',
    })
  })
})
