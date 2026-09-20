export interface ParsedSseEvent {
  event: string
  data: string
}

export class SseStreamParser {
  private buffer = ''

  push(chunk: string): ParsedSseEvent[] {
    this.buffer = `${this.buffer}${chunk}`.replace(/\r\n/g, '\n')
    const events: ParsedSseEvent[] = []
    let boundary = this.buffer.indexOf('\n\n')

    while (boundary >= 0) {
      const block = this.buffer.slice(0, boundary)
      this.buffer = this.buffer.slice(boundary + 2)
      const event = parseSseBlock(block)
      if (event) {
        events.push(event)
      }
      boundary = this.buffer.indexOf('\n\n')
    }

    return events
  }

  finish(): ParsedSseEvent[] {
    if (!this.buffer.trim()) {
      this.buffer = ''
      return []
    }
    const event = parseSseBlock(this.buffer)
    this.buffer = ''
    return event ? [event] : []
  }
}

export function parseSseBlock(block: string): ParsedSseEvent | null {
  let event = 'message'
  const data: string[] = []

  for (const line of block.split('\n')) {
    if (line.startsWith(':')) {
      continue
    }
    if (line.startsWith('event:')) {
      event = line.slice('event:'.length).trim()
      continue
    }
    if (line.startsWith('data:')) {
      data.push(line.slice('data:'.length).trimStart())
    }
  }

  if (!data.length) {
    return null
  }
  return { event, data: data.join('\n') }
}
