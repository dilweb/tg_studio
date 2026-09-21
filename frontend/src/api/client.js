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

export { authHeaders }

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
        // FormData брайзер сам ставит Content-Type с boundary — не мешаем
        ...(options.body && !(options.body instanceof FormData)
          ? { 'Content-Type': 'application/json' }
          : {}),
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

/**
 * Binary-запрос (фото чата): заголовки авторизации <img> не умеет,
 * поэтому качаем blob и отдаём во view через object URL.
 */
export async function apiFetchBlob(path) {
  let response
  try {
    response = await fetch(path, { headers: authHeaders() })
  } catch {
    const err = new Error('API недоступен')
    err.kind = 'network'
    throw err
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
  return response.blob()
}

export function fetchMe() {
  return apiFetch('/api/auth/me')
}

/** Короткие REST-обёртки над apiFetch. */
export const api = {
  get: (path) => apiFetch(path),
  post: (path, body) => apiFetch(path, { method: 'POST', body: JSON.stringify(body) }),
  postForm: (path, formData) => apiFetch(path, { method: 'POST', body: formData }),
  patch: (path, body) => apiFetch(path, { method: 'PATCH', body: JSON.stringify(body) }),
  put: (path, body) => apiFetch(path, { method: 'PUT', body: JSON.stringify(body) }),
  del: (path) => apiFetch(path, { method: 'DELETE' }),
}
