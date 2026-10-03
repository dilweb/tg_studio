// SSE-клиент AI-чата: EventSource не умеет POST и auth-заголовки,
// поэтому стрим читаем через fetch + ReadableStream (кадры `data: {json}\n\n`).

import { authHeaders } from './client'

/**
 * POST /api/admin/ai-chat/stream.
 * onEvent(event) — распарсенный кадр с полем `type`:
 * start | turn | tool_call | nudge | delta | final | error.
 */
export async function streamAiChat(payload, { onEvent, signal } = {}) {
  let response
  try {
    response = await fetch('/api/admin/ai-chat/stream', {
      method: 'POST',
      headers: { ...authHeaders(), 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
      signal,
    })
  } catch (err) {
    if (err.name === 'AbortError') return
    const e = new Error('API недоступен')
    e.kind = 'network'
    throw e
  }

  if (!response.ok) {
    let detail = `Ошибка ${response.status}`
    try {
      detail = (await response.json())?.detail ?? detail
    } catch {
      // тело не json
    }
    const err = new Error(detail)
    err.status = response.status
    err.detail = detail
    throw err
  }

  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''

  const dispatch = (frame) => {
    for (const line of frame.split('\n')) {
      const trimmed = line.trim()
      if (!trimmed.startsWith('data:')) continue
      try {
        onEvent(JSON.parse(trimmed.slice(5).trim()))
      } catch {
        // мусорный кадр — пропускаем
      }
    }
  }

  while (true) {
    const { done, value } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })
    let sep
    while ((sep = buffer.indexOf('\n\n')) !== -1) {
      const frame = buffer.slice(0, sep)
      buffer = buffer.slice(sep + 2)
      dispatch(frame)
    }
  }
  // хвост без завершающего \n\n
  dispatch(buffer)
}
