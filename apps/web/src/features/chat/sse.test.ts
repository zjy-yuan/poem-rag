import { describe, expect, it } from 'vitest'

import { SseStreamParser } from './sse'

describe('SseStreamParser', () => {
  it('parses events split across chunks', () => {
    const parser = new SseStreamParser()

    expect(parser.push('event: meta\ndata: {"message')).toEqual([])
    expect(parser.push('_id":7}\n\nevent: delta\ndata: {"text":"明月"}\n\n')).toEqual([
      { event: 'meta', data: '{"message_id":7}' },
      { event: 'delta', data: '{"text":"明月"}' },
    ])
  })

  it('normalizes CRLF and flushes a final event', () => {
    const parser = new SseStreamParser()

    expect(parser.push('event: done\r\ndata: {"finish_reason":"stop"}\r\n\r\n')).toEqual([
      { event: 'done', data: '{"finish_reason":"stop"}' },
    ])
    expect(parser.finish()).toEqual([])
  })

  it('joins multiline data fields', () => {
    const parser = new SseStreamParser()

    expect(parser.push('event: note\ndata: first\ndata: second')).toEqual([])
    expect(parser.finish()).toEqual([{ event: 'note', data: 'first\nsecond' }])
  })
})
