import { reactive } from 'vue'

import { fetchMe, hasInitData } from '../api/client'

const ME_KEY = 'tg_studio.me'
const DEBUG_ID_KEY = 'tg_studio.debugUserId'

// Роли, которые понимает роутер (nav.js). Всё остальное — протухший кэш
// со старой схемы ролей: его выбрасываем, иначе guard уводит в /login и зацикливается.
const VALID_ROLES = new Set(['owner', 'master'])

function loadMe() {
  try {
    const raw = localStorage.getItem(ME_KEY)
    const me = raw ? JSON.parse(raw) : null
    if (me && !VALID_ROLES.has(me.role)) return null
    return me
  } catch {
    return null
  }
}

// Простой reactive-store без pinia: сессия живёт в localStorage — F5 не выкидывает.
export const authStore = reactive({
  me: loadMe(),
  loading: false,

  get user() {
    return this.me
  },

  setMe(me) {
    this.me = me
    localStorage.setItem(ME_KEY, JSON.stringify(me))
  },

  setDebugUserId(id) {
    localStorage.setItem(DEBUG_ID_KEY, String(id))
  },

  logout() {
    this.me = null
    localStorage.removeItem(ME_KEY)
    localStorage.removeItem(DEBUG_ID_KEY)
  },
})

/**
 * Попытка входа: initData (Telegram) или debug-заголовок (браузер).
 * Возвращает me или бросает ошибку из apiFetch.
 */
export async function login() {
  authStore.loading = true
  try {
    const me = await fetchMe()
    authStore.setMe(me)
    return me
  } finally {
    authStore.loading = false
  }
}

/**
 * Фоновая ревалидация сессии (App.vue при монтировании).
 * Протухший localStorage-кэш чистится, реально активную сессию не трогаем.
 */
export async function refreshMe() {
  if (!authStore.me) return
  try {
    const me = await fetchMe()
    authStore.setMe(me)
  } catch (err) {
    if (err.status === 401 || err.status === 403) {
      // В Telegram не разлогиниваемся из-за сетевого сбоя — только по ответу API
      if (!hasInitData() || err.status === 403) {
        authStore.logout()
      }
    }
  }
}
