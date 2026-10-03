<script setup>
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from 'vue'

import { streamAiChat } from '../api/aiSse'
import { api } from '../api/client'

// Песочница AI-агента: один интерфейс — два промпта (владелец/клиент).
// Диалоги разведены в БД (AIConversation.user_id vs client_id), на фронте —
// отдельные хранилища в localStorage (GET-эндпоинта истории нет).

const VARIANT_KEY = 'aiPlayground.v1.variant'
const CLIENT_ID_KEY = 'aiPlayground.v1.clientId'

const variant = ref(localStorage.getItem(VARIANT_KEY) === 'client' ? 'client' : 'owner')
const clients = ref([])
const clientId = ref(Number(localStorage.getItem(CLIENT_ID_KEY)) || null)

const messages = ref([])
const conversationId = ref(null)
const draft = ref('')
const streaming = ref(false)
const streamingText = ref('')
const error = ref('')

const showLog = ref(false)
const events = ref([])
const logEl = ref(null)
const messagesEl = ref(null)
let controller = null
let startedThisSend = false

function storeKey() {
  return variant.value === 'owner'
    ? 'aiPlayground.v1.owner'
    : `aiPlayground.v1.client.${clientId.value}`
}

function loadStore() {
  try {
    const data = JSON.parse(localStorage.getItem(storeKey()) ?? 'null')
    messages.value = Array.isArray(data?.messages) ? data.messages : []
    conversationId.value = data?.conversationId ?? null
  } catch {
    messages.value = []
    conversationId.value = null
  }
}

function saveStore() {
  localStorage.setItem(
    storeKey(),
    JSON.stringify({ conversationId: conversationId.value, messages: messages.value }),
  )
}

function abortStream() {
  if (controller) {
    controller.abort()
    controller = null
  }
}

function pickVariant(v) {
  if (v === variant.value) return
  abortStream()
  variant.value = v
  localStorage.setItem(VARIANT_KEY, v)
  error.value = ''
  showHistory.value = false
  if (v === 'client' && !clientId.value && clients.value.length) {
    clientId.value = clients.value[0].id
    localStorage.setItem(CLIENT_ID_KEY, String(clientId.value))
  }
  loadStore()
}

function pickClient() {
  abortStream()
  localStorage.setItem(CLIENT_ID_KEY, String(clientId.value))
  error.value = ''
  showHistory.value = false
  loadStore()
}

function newDialog() {
  abortStream()
  messages.value = []
  conversationId.value = null
  saveStore()
  error.value = ''
}

function formatHistoryDate(iso) {
  if (!iso) return ''
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return ''
  const now = new Date()
  const sameDay = d.toDateString() === now.toDateString()
  const time = d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
  if (sameDay) return `сегодня ${time}`
  return `${d.toLocaleDateString([], { day: '2-digit', month: '2-digit' })} ${time}`
}

const showHistory = ref(false)
const historyLoading = ref(false)
const history = ref([])

async function loadHistory() {
  historyLoading.value = true
  error.value = ''
  try {
    const params = new URLSearchParams({ variant: variant.value })
    if (variant.value === 'client') params.set('client_id', String(clientId.value))
    history.value = await api.get(`/api/admin/ai-chat/conversations?${params}`)
  } catch (err) {
    error.value = err.detail ?? err.message
    history.value = []
  } finally {
    historyLoading.value = false
  }
}

function toggleHistory() {
  showHistory.value = !showHistory.value
  if (showHistory.value) loadHistory()
}

async function openConversation(id) {
  error.value = ''
  abortStream()
  try {
    const params = new URLSearchParams({ variant: variant.value })
    if (variant.value === 'client') params.set('client_id', String(clientId.value))
    const msgs = await api.get(
      `/api/admin/ai-chat/conversations/${id}/messages?${params}`,
    )
    messages.value = msgs
    conversationId.value = id
    saveStore()
    showHistory.value = false
    scrollToBottom()
  } catch (err) {
    error.value = err.detail ?? err.message
  }
}

function logEvent(ev) {  events.value.push(ev)
  if (events.value.length > 200) events.value.splice(0, events.value.length - 200)
  nextTick(() => {
    if (logEl.value) logEl.value.scrollTop = logEl.value.scrollHeight
  })
}

async function scrollToBottom() {
  await nextTick()
  if (messagesEl.value) messagesEl.value.scrollTop = messagesEl.value.scrollHeight
}

const canSend = computed(() => {
  if (streaming.value) return false
  if (variant.value === 'client' && !clientId.value) return false
  return draft.value.trim().length > 0
})

function handleStreamError(message) {
  if (message.includes('Диалог не найден')) {
    // протухший conversation_id — сбрасываем хранилище варианта
    messages.value = []
    conversationId.value = null
    saveStore()
    error.value = 'Диалог не найден — хранилище сброшено, начните заново'
  } else {
    error.value = message
  }
}

async function send() {
  const text = draft.value.trim()
  if (!text || !canSend.value) return
  error.value = ''
  streaming.value = true
  streamingText.value = ''
  startedThisSend = false

  messages.value.push({ role: 'user', content: text })
  saveStore()
  draft.value = ''
  scrollToBottom()

  const payload = { message: text, variant: variant.value }
  if (conversationId.value) payload.conversation_id = conversationId.value
  if (variant.value === 'client') payload.client_id = clientId.value

  controller = new AbortController()
  try {
    await streamAiChat(payload, {
      signal: controller.signal,
      onEvent: (ev) => {
        if (ev.type !== 'delta' && ev.type !== 'start') logEvent(ev)
        switch (ev.type) {
          case 'start':
            startedThisSend = true
            break
          case 'delta':
            streamingText.value += ev.text
            scrollToBottom()
            break
          case 'final': {
            // финальный текст заменяет стримящийся пузырь, не аппендится
            messages.value.push({ role: 'assistant', content: ev.response ?? '' })
            conversationId.value = ev.conversation_id
            saveStore()
            streamingText.value = ''
            scrollToBottom()
            break
          }
          case 'error':
            handleStreamError(ev.message ?? 'Ошибка стрима')
            break
          default:
            break
        }
      },
    })
    // стрим кончился без final и без error — обрыв соединения
    if (streamingText.value) {
      handleStreamError('Стрим оборвался без финального ответа')
    }
  } catch (err) {
    if (err.name === 'AbortError') {
      // наш abort (unmount/переключение) — тихо
    } else if (!startedThisSend) {
      // до стрима не дошло: сервер сообщение не сохранял — убираем из UI
      const last = messages.value[messages.value.length - 1]
      if (last?.role === 'user') messages.value.pop()
      saveStore()
      error.value = err.detail ?? err.message
    } else {
      error.value = err.detail ?? err.message
    }
  } finally {
    controller = null
    streaming.value = false
    streamingText.value = ''
  }
}

onMounted(async () => {
  loadStore()
  try {
    clients.value = await api.get('/api/admin/ai-chat/clients')
  } catch {
    // селектор клиента не критичен для варианта owner
  }
  if (variant.value === 'client' && !clientId.value && clients.value.length) {
    clientId.value = clients.value[0].id
    localStorage.setItem(CLIENT_ID_KEY, String(clientId.value))
    loadStore()
  }
})

onUnmounted(() => {
  abortStream()
})

watch(showLog, async (open) => {
  if (open) {
    await nextTick()
    if (logEl.value) logEl.value.scrollTop = logEl.value.scrollHeight
  }
})
</script>

<template>
  <div class="ai-view">
    <div v-if="error" class="error-box">{{ error }}</div>

    <div class="ai-toolbar">
      <div class="segmented">
        <button
          type="button"
          class="seg-btn"
          :class="{ active: variant === 'owner' }"
          @click="pickVariant('owner')"
        >
          Владелец
        </button>
        <button
          type="button"
          class="seg-btn"
          :class="{ active: variant === 'client' }"
          @click="pickVariant('client')"
        >
          Клиент
        </button>
      </div>

      <select
        v-if="variant === 'client'"
        v-model.number="clientId"
        class="client-select"
        @change="pickClient"
      >
        <option v-if="!clients.length" :value="null" disabled>Клиентов нет</option>
        <option v-for="c in clients" :key="c.id" :value="c.id">{{ c.full_name }}</option>
      </select>

      <div class="spacer"></div>
      <button class="btn btn-ghost btn-sm" type="button" @click="showLog = !showLog">
        {{ showLog ? 'Скрыть журнал' : 'Журнал событий' }}
      </button>
      <button
        class="btn btn-ghost btn-sm"
        type="button"
        :disabled="streaming"
        @click="toggleHistory"
      >
        История диалогов
      </button>
      <button class="btn btn-sm" type="button" :disabled="streaming" @click="newDialog">
        Новый диалог
      </button>
    </div>

    <div v-if="showHistory" class="history-panel">
      <div v-if="historyLoading" class="muted log-empty">Загрузка…</div>
      <div v-else-if="!history.length" class="muted log-empty">Диалогов пока нет</div>
      <button
        v-for="h in history"
        :key="h.id"
        type="button"
        class="history-item"
        :class="{ active: h.id === conversationId }"
        @click="openConversation(h.id)"
      >
        <span class="history-title">{{ h.title }}</span>
        <span class="history-meta muted">
          {{ h.message_count }} сообщ. · {{ formatHistoryDate(h.updated_at) }}
        </span>
      </button>
    </div>

    <div ref="messagesEl" class="ai-messages">
      <div v-if="!messages.length && !streamingText" class="ai-empty muted">
        <div class="ai-empty-title">
          {{ variant === 'owner' ? 'Спросите что-нибудь про данные' : 'Тест от лица клиента' }}
        </div>
        <div>
          {{
            variant === 'owner'
              ? 'Например: «сколько записей у Маши на этой неделе»'
              : 'Клиентский промпт — заглушка, текст итерируем здесь'
          }}
        </div>
      </div>
      <div
        v-for="(m, i) in messages"
        :key="i"
        class="bubble-row"
        :class="m.role === 'user' ? 'mine' : 'theirs'"
      >
        <div class="bubble">
          <div class="bubble-text">{{ m.content || '—' }}</div>
        </div>
      </div>
      <div v-if="streamingText" class="bubble-row theirs">
        <div class="bubble">
          <div class="bubble-text">{{ streamingText }}▌</div>
        </div>
      </div>
    </div>

    <div v-if="showLog" ref="logEl" class="event-log">
      <div v-if="!events.length" class="muted log-empty">
        Пусто — события цикла появятся при отправке
      </div>
      <template v-for="(ev, i) in events" :key="i">
        <div v-if="ev.type === 'turn'" class="log-line muted">⭮ раунд {{ ev.round }}</div>
        <div v-else-if="ev.type === 'tool_call'" class="log-line">
          <span class="log-tag">{{ ev.sql ? 'SQL' : 'TOOL' }}</span>{{ ev.name }}
          <pre v-if="ev.sql" class="log-sql">{{ ev.sql }}</pre>
          <pre
            v-else-if="ev.args && Object.keys(ev.args).length"
            class="log-sql"
            >{{ JSON.stringify(ev.args, null, 2) }}</pre
          >
        </div>
        <div v-else-if="ev.type === 'nudge'" class="log-line log-nudge">⚑ нудж: {{ ev.kind }}</div>
        <div v-else-if="ev.type === 'error'" class="log-line log-err">✖ {{ ev.message }}</div>
        <div v-else-if="ev.type === 'final'" class="log-line muted">■ финал</div>
        <div v-else class="log-line muted">{{ ev.type }}</div>
      </template>
    </div>

    <form class="composer" @submit.prevent="send">
      <input
        v-model="draft"
        class="composer-input"
        type="text"
        placeholder="Сообщение…"
        autocomplete="off"
      />
      <button class="btn btn-primary btn-sm" type="submit" :disabled="!canSend">
        {{ streaming ? '…' : 'Отправить' }}
      </button>
    </form>
  </div>
</template>

<style scoped>
.ai-view {
  height: 100%;
  display: flex;
  flex-direction: column;
}

.ai-toolbar {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 12px;
  flex-wrap: wrap;
}

.segmented {
  display: inline-flex;
  border: 1px solid var(--border);
  border-radius: 8px;
  overflow: hidden;
}

.seg-btn {
  background: var(--surface);
  color: var(--muted);
  border: none;
  padding: 7px 14px;
  font-size: 13px;
  cursor: pointer;
}

.seg-btn + .seg-btn {
  border-left: 1px solid var(--border);
}

.seg-btn.active {
  background: rgba(108, 99, 255, 0.16);
  color: var(--accent);
  font-weight: 600;
}

.client-select {
  max-width: 240px;
}

/* --- история диалогов --- */
.history-panel {
  display: flex;
  flex-direction: column;
  gap: 6px;
  max-height: 260px;
  overflow-y: auto;
  border: 1px solid var(--border);
  border-radius: 10px;
  background: var(--surface2);
  padding: 8px;
  margin-bottom: 12px;
}

.history-item {
  display: flex;
  flex-direction: column;
  gap: 2px;
  text-align: left;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 8px 10px;
  cursor: pointer;
  font-size: 13px;
  color: var(--text, inherit);
}

.history-item:hover {
  border-color: var(--accent);
}

.history-item.active {
  border-color: var(--accent);
  background: rgba(108, 99, 255, 0.12);
}

.history-title {
  font-weight: 600;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.history-meta {
  font-size: 11.5px;
}

/* --- переписка --- */
.ai-messages {
  flex: 1;
  overflow-y: auto;
  padding: 14px;
  display: flex;
  flex-direction: column;
  gap: 8px;
  min-height: 0;
  border: 1px solid var(--border);
  border-radius: 12px;
  background: var(--surface);
}

.ai-empty {
  margin: auto;
  text-align: center;
}

.ai-empty-title {
  font-weight: 600;
  margin-bottom: 4px;
}

.bubble-row {
  display: flex;
}

.bubble-row.mine {
  justify-content: flex-end;
}

.bubble-row.theirs {
  justify-content: flex-start;
}

.bubble {
  max-width: 80%;
  border-radius: 12px;
  padding: 8px 12px;
  font-size: 13.5px;
  line-height: 1.45;
}

.mine .bubble {
  background: rgba(108, 99, 255, 0.22);
  border: 1px solid rgba(108, 99, 255, 0.35);
}

.theirs .bubble {
  background: var(--surface2);
  border: 1px solid var(--border);
}

.bubble-text {
  white-space: pre-wrap;
  word-break: break-word;
}

/* --- журнал событий --- */
.event-log {
  border: 1px solid var(--border);
  border-radius: 10px;
  background: var(--surface2);
  padding: 10px 12px;
  max-height: 220px;
  overflow-y: auto;
  margin-top: 10px;
  font-size: 12px;
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.log-empty {
  text-align: center;
  padding: 4px;
}

.log-line {
  line-height: 1.4;
}

.log-tag {
  font-weight: 700;
  color: var(--accent);
  margin-right: 6px;
}

.log-sql {
  margin: 2px 0 0;
  padding: 6px 8px;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 6px;
  font-size: 11.5px;
  white-space: pre-wrap;
  word-break: break-all;
}

.log-nudge {
  color: var(--accent);
}

.log-err {
  color: #e0566a;
}

/* --- композер --- */
.composer {
  display: flex;
  gap: 8px;
  margin-top: 12px;
}

.composer-input {
  flex: 1;
}

@media (max-width: 768px) {
  .event-log {
    max-height: 160px;
  }
}
</style>
