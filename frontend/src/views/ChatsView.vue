<script setup>
import { computed, nextTick, onMounted, onUnmounted, reactive, ref } from 'vue'

import { api, apiFetchBlob } from '../api/client'

// Список чатов слева, переписка справа; на узких экранах — по одному
const threads = ref([])
const activeClientId = ref(null)
const messages = ref([])
const activeThread = computed(() =>
  threads.value.find((t) => t.client_id === activeClientId.value) ?? null,
)

const draft = ref('')
const photoFile = ref(null) // File | null — приложенное фото
const sending = ref(false)
const error = ref('')
const loading = ref(true)

// URL blob'ов медиа (фото + голосовые; заголовки авторизации <img>/<audio> не умеют)
// + revoke при уходе
const mediaUrls = reactive({})
let pollTimer = null
let ticking = false

const messagesEl = ref(null)

// Какие file_kind как рендерим
const VIDEO_KINDS = ['video', 'video_note', 'animation']
const AUDIO_KINDS = ['voice', 'audio']

function isVideo(m) {
  return VIDEO_KINDS.includes(m.file_kind)
}

function isAudio(m) {
  return AUDIO_KINDS.includes(m.file_kind)
}

function isFile(m) {
  return m.file_kind === 'document'
}

function docName(m) {
  return m.content || 'файл'
}

// Blob-ссылки живут только внутри страницы: браузер/вебвью пытается их
// «открыть» и предлагает найти приложение (macOS: «Do you want to open
// blob:…», на телефоне так же). Поэтому документ идёт по прямой подписанной
// ссылке: в Telegram открываем её через openLink в системном браузере (там
// скачивание штатное), в обычном браузере оставляем дефолт — браузер скачает
// сам (Content-Disposition: attachment).
function openDocument(m, event) {
  const webApp = window.Telegram?.WebApp
  if (!webApp?.openLink) return
  event.preventDefault()
  webApp.openLink(new URL(m.file_url, window.location.href).href)
}

// Элемент не смог декодировать blob (формат не поддержан вебвью, битые
// байты) — показываем заглушку вместо мёртвого плеера
function mediaFailed(id) {
  mediaUrls[id] = 'error'
}

function previewText(t) {
  const prefix = t.last_direction === 'from_master' ? 'Вы: ' : t.last_direction === 'from_ai' ? 'ИИ: ' : ''
  return prefix + (t.last_message_preview || '—')
}

// Ответ AI-агента — свой класс пузырька, у закреплённого чата — бейдж
function bubbleSide(m) {
  if (m.direction === 'from_master') return 'mine'
  if (m.direction === 'from_ai') return 'ai'
  return 'theirs'
}

// Подпись над сообщением: имя мастера (или «Мастер», если чат не закреплён)
// и «ИИ ассистент» для ответов агента; у клиента подписи нет
function senderLabel(m) {
  if (m.direction === 'from_ai') return 'ИИ ассистент'
  if (m.direction === 'from_master') {
    return activeThread.value?.assigned_master_name || 'Мастер'
  }
  return null
}

function timeShort(iso) {
  const d = new Date(iso)
  const today = new Date()
  const sameDay = d.toDateString() === today.toDateString()
  const hm = `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`
  return sameDay ? hm : `${String(d.getDate()).padStart(2, '0')}.${String(d.getMonth() + 1).padStart(2, '0')} ${hm}`
}

async function loadThreads() {
  try {
    threads.value = await api.get('/api/admin/chats')
    // выбранный чат мог исчезнуть (не должен, но на всякий случай)
    if (activeClientId.value && !threads.value.some((t) => t.client_id === activeClientId.value)) {
      activeClientId.value = null
      messages.value = []
    }
  } catch (err) {
    error.value = err.detail ?? err.message
  }
}

const pendingMedia = new Set() // id, чей blob ещё качается — тик не должен плодить параллельные fetch

async function fetchMedia(messageId) {
  if (mediaUrls[messageId] || pendingMedia.has(messageId)) return
  pendingMedia.add(messageId)
  try {
    const blob = await apiFetchBlob(`/api/admin/chats/files/${messageId}`)
    mediaUrls[messageId] = URL.createObjectURL(blob)
  } catch (err) {
    // не удалось скачать — показываем плейсхолдер, чат не ломаем
    mediaUrls[messageId] = 'error'
  } finally {
    pendingMedia.delete(messageId)
  }
}

async function scrollToBottom() {
  await nextTick()
  const el = messagesEl.value
  if (el) el.scrollTop = el.scrollHeight
}

async function loadMessages(animateScroll = false) {
  if (!activeClientId.value) return
  try {
    messages.value = await api.get(`/api/admin/chats/${activeClientId.value}/messages`)
    for (const m of messages.value) {
      // фото/голос/видео качаем в blob сразу; документ — нет: он идёт по
      // прямой подписанной ссылке (m.file_url) и качается только по клику
      if (m.file_kind && m.file_kind !== 'document') fetchMedia(m.id)
    }
    if (animateScroll) await scrollToBottom()
  } catch (err) {
    error.value = err.detail ?? err.message
  }
}

async function markRead() {
  if (!activeClientId.value) return
  try {
    await api.post(`/api/admin/chats/${activeClientId.value}/read`, {})
    const t = threads.value.find((t) => t.client_id === activeClientId.value)
    if (t) t.unread_count = 0
  } catch {
    // непрочитанные — некритично
  }
}

async function openChat(id) {
  activeClientId.value = id
  messages.value = []
  await loadMessages(true)
  await markRead()
}

function closeChat() {
  activeClientId.value = null
  messages.value = []
}

async function tick() {
  if (ticking) return
  ticking = true
  try {
    await loadThreads()
    if (activeClientId.value) {
      const t = threads.value.find((t) => t.client_id === activeClientId.value)
      if (t?.unread_count > 0) await markRead()
      await loadMessages()
    }
  } finally {
    ticking = false
  }
}

function pickPhoto(event) {
  photoFile.value = event.target.files?.[0] ?? null
  // позволяем выбрать тот же файл повторно после отмены отправки
  event.target.value = ''
}

function clearPhoto() {
  photoFile.value = null
}

async function send() {
  if (!activeClientId.value || sending.value) return
  const text = draft.value.trim()
  if (!text && !photoFile.value) return
  sending.value = true
  error.value = ''
  try {
    let created
    if (photoFile.value) {
      const fd = new FormData()
      fd.append('file', photoFile.value)
      if (text) fd.append('caption', text)
      created = await api.postForm(`/api/admin/chats/${activeClientId.value}/photo`, fd)
    } else {
      created = await api.post(`/api/admin/chats/${activeClientId.value}/messages`, {
        content: text,
      })
    }
    messages.value.push(created)
    draft.value = ''
    photoFile.value = null
    const t = threads.value.find((t) => t.client_id === activeClientId.value)
    if (t) {
      t.last_message_preview = created.content || '📷 Фото'
      t.last_direction = 'from_master'
      t.last_message_at = created.created_at
    }
    await scrollToBottom()
    loadThreads() // фоновой пересбор списка, не ждём
  } catch (err) {
    // 502 — Telegram не принял: текст/фото остаются в композере для повтора
    error.value = err.detail ?? err.message
  } finally {
    sending.value = false
  }
}

onMounted(async () => {
  await loadThreads()
  loading.value = false
  pollTimer = setInterval(tick, 5000)
})

onUnmounted(() => {
  if (pollTimer) clearInterval(pollTimer)
  for (const url of Object.values(mediaUrls)) {
    if (url !== 'error') URL.revokeObjectURL(url)
  }
})
</script>

<template>
  <div class="chats-view">
    <div v-if="error" class="error-box">{{ error }}</div>
    <div v-if="loading" class="muted">Загрузка…</div>

    <div v-else class="chats-layout">
      <!-- Список чатов -->
      <div class="chat-list" :class="{ hidden: activeClientId }">
        <div v-if="!threads.length" class="card">
          <div class="empty-state">
            <div class="empty-title">Чатов пока нет</div>
            <div>
              Клиенты пишут боту — их сообщения появятся здесь автоматически
            </div>
          </div>
        </div>

        <button
          v-for="t in threads"
          :key="t.client_id"
          type="button"
          class="chat-item"
          :class="{ active: t.client_id === activeClientId }"
          @click="openChat(t.client_id)"
        >
          <div class="chat-item-main">
            <div class="chat-item-top">
              <span class="chat-name">{{ t.full_name }}</span>
              <span class="chat-time">{{ timeShort(t.last_message_at) }}</span>
            </div>
            <div class="chat-item-bottom">
              <span class="chat-preview">{{ previewText(t) }}</span>
              <span v-if="t.unread_count" class="unread-badge">{{ t.unread_count }}</span>
            </div>
          </div>
        </button>
      </div>

      <!-- Переписка -->
      <div class="chat-pane" :class="{ open: activeClientId }">
        <template v-if="activeClientId">
          <div class="chat-header">
            <button class="btn btn-sm btn-ghost back-btn" type="button" @click="closeChat">
              ← Назад
            </button>
            <div>
              <div class="chat-header-name">
                {{ activeThread?.full_name ?? '…' }}
                <span
                  v-if="activeThread?.assigned_master_name"
                  class="assigned-badge"
                  title="Чат закреплён за мастером после эскалации AI-агентом"
                >📌 {{ activeThread.assigned_master_name }}</span>
              </div>
              <div v-if="activeThread?.username" class="chat-header-username">
                @{{ activeThread.username }}
              </div>
            </div>
          </div>

          <div ref="messagesEl" class="chat-messages">
            <div v-if="!messages.length" class="muted" style="text-align: center">
              Сообщений ещё нет
            </div>
            <div
              v-for="m in messages"
              :key="m.id"
              class="bubble-row"
              :class="bubbleSide(m)"
            >
              <div class="bubble">
                <div v-if="senderLabel(m)" class="bubble-sender">{{ senderLabel(m) }}</div>
                <img
                  v-if="m.file_kind === 'photo' && mediaUrls[m.id] && mediaUrls[m.id] !== 'error'"
                  :src="mediaUrls[m.id]"
                  class="bubble-photo"
                  alt="фото"
                  @error="mediaFailed(m.id)"
                />
                <div
                  v-else-if="m.file_kind === 'photo'"
                  class="bubble-media-fallback muted"
                >
                  📷 фото недоступно
                </div>
                <video
                  v-else-if="isVideo(m) && mediaUrls[m.id] && mediaUrls[m.id] !== 'error'"
                  controls
                  playsinline
                  preload="metadata"
                  :src="mediaUrls[m.id]"
                  class="bubble-video"
                  @error="mediaFailed(m.id)"
                ></video>
                <div
                  v-else-if="isVideo(m)"
                  class="bubble-media-fallback muted"
                >
                  📎 видео недоступно
                </div>
                <audio
                  v-else-if="isAudio(m) && mediaUrls[m.id] && mediaUrls[m.id] !== 'error'"
                  controls
                  preload="metadata"
                  :src="mediaUrls[m.id]"
                  class="bubble-audio"
                  @error="mediaFailed(m.id)"
                ></audio>
                <div
                  v-else-if="isAudio(m)"
                  class="bubble-media-fallback muted"
                >
                  🎙 аудио недоступно
                </div>
                <a
                  v-else-if="isFile(m) && m.file_url"
                  :href="m.file_url"
                  :download="docName(m)"
                  class="bubble-file"
                  @click="openDocument(m, $event)"
                >⬇️ {{ docName(m) }}</a>
                <div
                  v-else-if="isFile(m)"
                  class="bubble-media-fallback muted"
                >
                  📎 файл недоступен
                </div>
                <div v-if="m.content && !isFile(m)" class="bubble-text">{{ m.content }}</div>
                <div class="bubble-time">{{ timeShort(m.created_at) }}</div>
              </div>
            </div>
          </div>

          <form class="composer" @submit.prevent="send">
            <div v-if="photoFile" class="composer-attachment">
              📷 {{ photoFile.name }}
              <button type="button" class="attach-remove" @click="clearPhoto">✕</button>
            </div>
            <div class="composer-row">
              <label class="attach-btn" title="Прикрепить фото">
                📎
                <input type="file" accept="image/*" hidden @change="pickPhoto" />
              </label>
              <input
                v-model="draft"
                class="composer-input"
                type="text"
                placeholder="Сообщение…"
                autocomplete="off"
              />
              <button class="btn btn-primary btn-sm" type="submit" :disabled="sending">
                {{ sending ? '…' : 'Отправить' }}
              </button>
            </div>
          </form>
        </template>
        <div v-else class="card chat-placeholder">
          <div class="empty-state">
            <div class="empty-title">Выберите чат</div>
            <div>Слева — клиенты, которые писали боту</div>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.chats-view {
  height: 100%;
  display: flex;
  flex-direction: column;
}

.chats-layout {
  display: grid;
  grid-template-columns: 300px 1fr;
  gap: 16px;
  flex: 1;
  min-height: 0;
}

/* --- список чатов --- */
.chat-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
  overflow-y: auto;
  min-height: 0;
}

.chat-item {
  display: block;
  width: 100%;
  text-align: left;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 10px;
  padding: 10px 12px;
  cursor: pointer;
  color: var(--text);
  transition: background 0.15s, border-color 0.15s;
}

.chat-item:hover {
  background: var(--surface2);
}

.chat-item.active {
  border-color: var(--accent);
  background: rgba(108, 99, 255, 0.12);
}

.chat-item-top {
  display: flex;
  justify-content: space-between;
  align-items: baseline;
  gap: 8px;
  margin-bottom: 4px;
}

.chat-name {
  font-weight: 600;
  font-size: 13.5px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.chat-time {
  font-size: 11px;
  color: var(--muted);
  flex-shrink: 0;
}

.chat-item-bottom {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 8px;
}

.chat-preview {
  font-size: 12.5px;
  color: var(--muted);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.unread-badge {
  background: var(--accent);
  color: #fff;
  border-radius: 999px;
  min-width: 20px;
  height: 20px;
  padding: 0 6px;
  font-size: 11px;
  font-weight: 700;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
}

/* --- переписка --- */
.chat-pane {
  display: flex;
  flex-direction: column;
  border: 1px solid var(--border);
  border-radius: 12px;
  background: var(--surface);
  min-height: 0;
  overflow: hidden;
}

.chat-placeholder {
  margin: auto;
  width: auto;
}

.chat-header {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 10px 14px;
  border-bottom: 1px solid var(--border);
  background: var(--surface2);
}

.back-btn {
  display: none;
}

.chat-header-name {
  font-weight: 600;
  font-size: 14px;
}

.chat-header-username {
  font-size: 11.5px;
  color: var(--muted);
}

.chat-messages {
  flex: 1;
  overflow-y: auto;
  padding: 14px;
  display: flex;
  flex-direction: column;
  gap: 8px;
  min-height: 0;
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
  max-width: 75%;
  border-radius: 12px;
  padding: 8px 12px;
  font-size: 13.5px;
  line-height: 1.4;
}

.mine .bubble {
  background: rgba(108, 99, 255, 0.22);
  border: 1px solid rgba(108, 99, 255, 0.35);
}

.theirs .bubble {
  background: var(--surface2);
  border: 1px solid var(--border);
}

/* Ответ AI-агента записи: свой цвет, чтобы не путать с мастером */
.ai .bubble {
  background: rgba(0, 150, 136, 0.14);
  border: 1px solid rgba(0, 150, 136, 0.35);
}

/* Подпись отправителя над сообщением (жирным): имя мастера / ИИ ассистент */
.bubble-sender {
  font-size: 11px;
  font-weight: 700;
  color: var(--muted);
  margin-bottom: 3px;
}

.assigned-badge {
  display: inline-block;
  font-size: 10.5px;
  font-weight: 600;
  color: var(--text);
  background: rgba(0, 150, 136, 0.15);
  border: 1px solid rgba(0, 150, 136, 0.35);
  border-radius: 999px;
  padding: 1px 8px;
  margin-left: 6px;
  vertical-align: middle;
  white-space: nowrap;
}

.bubble-photo {
  max-width: 100%;
  max-height: 260px;
  border-radius: 8px;
  display: block;
  margin-bottom: 6px;
}

.bubble-media-fallback {
  font-size: 12px;
  margin-bottom: 4px;
}

.bubble-video {
  width: 240px;
  max-width: 100%;
  max-height: 260px;
  border-radius: 8px;
  display: block;
  margin-bottom: 6px;
  background: #000;
}

.bubble-file {
  display: inline-block;
  font-size: 13px;
  font-weight: 600;
  color: var(--accent);
  text-decoration: none;
  padding: 2px 0;
  word-break: break-all;
}

.bubble-audio {
  display: block;
  width: 240px;
  max-width: 100%;
  height: 40px;
  margin-bottom: 4px;
}

.bubble-text {
  white-space: pre-wrap;
  word-break: break-word;
}

.bubble-time {
  font-size: 10.5px;
  color: var(--muted);
  text-align: right;
  margin-top: 4px;
}

/* --- композер --- */
.composer {
  border-top: 1px solid var(--border);
  padding: 10px 12px;
  background: var(--surface2);
}

.composer-attachment {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 12.5px;
  color: var(--muted);
  margin-bottom: 8px;
}

.attach-remove {
  background: none;
  border: none;
  color: var(--muted);
  cursor: pointer;
  font-size: 13px;
  padding: 0;
}

.composer-row {
  display: flex;
  align-items: center;
  gap: 8px;
}

.attach-btn {
  cursor: pointer;
  font-size: 16px;
  padding: 4px 6px;
  border-radius: 6px;
}

.attach-btn:hover {
  background: var(--border);
}

.composer-input {
  flex: 1;
}

/* --- узкие экраны: показываем либо список, либо переписку --- */
@media (max-width: 768px) {
  .chats-layout {
    grid-template-columns: 1fr;
  }

  .chat-list.hidden {
    display: none;
  }

  .chat-pane {
    display: none;
  }

  .chat-pane.open {
    display: flex;
  }

  .back-btn {
    display: inline-flex;
  }
}
</style>
