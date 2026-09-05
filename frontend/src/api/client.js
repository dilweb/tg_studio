// Fetch-обёртка: Telegram initData → Authorization, иначе DEBUG-заголовок.

function tg() {
  return window.Telegram?.WebApp ?? null
}

export function hasInitData() {
  return Boolean(tg()?.initData)
}

/**
 * Заголовки для запросов к API.
 * Внутри Telegram — initData (единственный надёжный способ в проде).
 * В браузере (dev, DEBUG=true на бэкенде) — X-Debug-User-Id.
 */
function authHeaders() {
  const initData = tg()?.initData
  if (initData) {
    return { Authorization: `TelegramInitData ${initData}` }
  }
  const debugId = localStorage.getItem('tg_studio.debugUserId')
  if (debugId) {
    return { 'X-Debug-User-Id': debugId }
  }
  return {}
}

/**
 * Запрос к API. Бросает Error с полями status/detail при не-2xx.
 */
export async function apiFetch(path, options = {}) {
  let response
  try {
    response = await fetch(path, {
      ...options,
      headers: {
        ...authHeaders(),
        ...(options.body ? { 'Content-Type': 'application/json' } : {}),
        ...options.headers,
      },
    })
  } catch {
    const err = new Error('API недоступен')
    err.kind = 'network'
    throw err
  }

  let data = null
  try {
    data = await response.json()
  } catch {
    // пустое тело — не ошибка
  }

  if (!response.ok) {
    const err = new Error(data?.detail ?? `Ошибка ${response.status}`)
    err.status = response.status
    err.detail = data?.detail
    throw err
  }
  return data
}

export function fetchMe() {
  return apiFetch('/api/auth/me')
}

/** Короткие REST-обёртки над apiFetch. */
export const api = {
  get: (path) => apiFetch(path),
  post: (path, body) => apiFetch(path, { method: 'POST', body: JSON.stringify(body) }),
  patch: (path, body) => apiFetch(path, { method: 'PATCH', body: JSON.stringify(body) }),
  put: (path, body) => apiFetch(path, { method: 'PUT', body: JSON.stringify(body) }),
  del: (path) => apiFetch(path, { method: 'DELETE' }),
}
