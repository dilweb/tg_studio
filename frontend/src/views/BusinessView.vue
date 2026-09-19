<script setup>
import { computed, onMounted, ref } from 'vue'

import { api } from '../api/client'

const info = ref(null)
const loading = ref(true)
const error = ref('')

const gcalStatus = ref(null) // { connected, email, share_email }
const gcalLoading = ref(true)
const gcalBusy = ref(false)

const shareEmail = ref('')
const shareBusy = ref(false)
const shareResult = ref(null)

const shareMessage = computed(() => {
  if (!shareResult.value) return ''
  const ok = shareResult.value.shared.filter((c) => c.ok).length
  const total = shareResult.value.shared.length
  const failed = shareResult.value.shared.filter((c) => !c.ok)
  const base = `Доступ открыт для ${ok} из ${total} календарей. Письмо от Google придёт на ${shareResult.value.email}.`
  if (ok === total) return base
  return `${base} Ошибки: ${failed.map((f) => `${f.calendar_id} — ${f.error}`).join('; ')}`
})

async function loadGcalStatus() {
  gcalLoading.value = true
  try {
    gcalStatus.value = await api.get('/api/admin/google-calendar/status')
    if (gcalStatus.value?.share_email) shareEmail.value = gcalStatus.value.share_email
  } catch (err) {
    error.value = err.detail ?? err.message
  } finally {
    gcalLoading.value = false
  }
}

async function shareGcal() {
  error.value = ''
  shareBusy.value = true
  shareResult.value = null
  try {
    shareResult.value = await api.post('/api/admin/google-calendar/share', {
      email: shareEmail.value.trim(),
    })
  } catch (err) {
    error.value = err.detail ?? err.message
  } finally {
    shareBusy.value = false
  }
}

async function connectGcal() {
  error.value = ''
  gcalBusy.value = true
  try {
    await api.post('/api/admin/google-calendar/connect')
    await loadGcalStatus()
  } catch (err) {
    error.value = err.detail ?? err.message
  } finally {
    gcalBusy.value = false
  }
}

async function disconnectGcal() {
  error.value = ''
  gcalBusy.value = true
  try {
    await api.del('/api/admin/google-calendar/disconnect')
    await loadGcalStatus()
  } catch (err) {
    error.value = err.detail ?? err.message
  } finally {
    gcalBusy.value = false
  }
}

onMounted(async () => {
  try {
    info.value = await api.get('/api/admin/business')
  } catch (err) {
    error.value = err.detail ?? err.message
  } finally {
    loading.value = false
  }
  await loadGcalStatus()
})
</script>

<template>
  <div>
    <div v-if="error" class="error-box">{{ error }}</div>

    <div v-if="loading" class="muted">Загрузка…</div>

    <template v-else-if="info">
      <div class="card" style="margin-bottom: 16px">
        <div class="section-head">
          <h2>{{ info.name }}</h2>
          <span class="badge" :class="info.is_active ? 'badge-green' : 'badge-muted'">
            {{ info.is_active ? 'Активен' : 'Выключен' }}
          </span>
        </div>
        <div v-if="info.description" style="margin-bottom: 12px">{{ info.description }}</div>
        <div style="display: flex; gap: 24px; flex-wrap: wrap; font-size: 13px">
          <div>
            <div class="muted">Телефон</div>
            <div>{{ info.phone ?? '—' }}</div>
          </div>
          <div>
            <div class="muted">Telegram владельца</div>
            <div>{{ info.owner_telegram_id }}</div>
          </div>
        </div>
      </div>

      <div class="card">
        <div class="section-head">
          <h2>Google Calendar</h2>
          <span
            v-if="!gcalLoading"
            class="badge"
            :class="gcalStatus?.connected ? 'badge-green' : 'badge-yellow'"
          >
            {{ gcalStatus?.connected ? 'Подключен' : 'Не подключен' }}
          </span>
        </div>
        <p class="muted" style="font-size: 13px; margin-bottom: 16px">
          Сеансы мастеров синхронизируются с этим Google-аккаунтом, а свободные слоты
          вычисляются с учётом занятости в нём. У каждого мастера может быть свой
          отдельный календарь внутри этого аккаунта — настраивается в разделе «Мастера».
        </p>

        <div v-if="gcalLoading" class="muted">Загрузка…</div>
        <template v-else-if="gcalStatus?.connected">
          <div style="margin-bottom: 12px">
            Аккаунт: <strong>{{ gcalStatus.email }}</strong>
          </div>

          <div style="margin-bottom: 16px">
            <label
              class="muted"
              for="gcal-share-email"
              style="display: block; font-size: 13px; margin-bottom: 6px"
            >
              Ваш Google-адрес — записи появятся в вашем Google Calendar
            </label>
            <div style="display: flex; gap: 10px; align-items: center; flex-wrap: wrap">
              <input
                id="gcal-share-email"
                v-model="shareEmail"
                type="email"
                autocomplete="email"
                placeholder="you@gmail.com"
                style="max-width: 280px"
              />
              <button class="btn btn-primary" :disabled="shareBusy" @click="shareGcal">
                {{ shareBusy ? 'Открываем доступ…' : 'Открыть доступ' }}
              </button>
            </div>
            <p v-if="shareMessage" class="muted" style="font-size: 12px; margin-top: 8px">
              {{ shareMessage }}
              Если календарь не появился сам: calendar.google.com → «+» рядом с «Другие
              календари» → «Подписка на календарь» → адрес {{ gcalStatus.email }}.
            </p>
          </div>

          <button class="btn btn-ghost" :disabled="gcalBusy" @click="disconnectGcal">
            Отключить
          </button>
        </template>
        <template v-else>
          <div style="display: flex; gap: 10px; align-items: center">
            <button class="btn btn-primary" :disabled="gcalBusy" @click="connectGcal">
              Подключить Google Calendar
            </button>
            <button class="btn btn-sm btn-ghost" :disabled="gcalBusy" @click="loadGcalStatus">
              Обновить статус
            </button>
          </div>
          <p class="muted" style="font-size: 12px; margin-top: 8px">
            Подключение через сервисный аккаунт студии — без окна Google. После
            подключения создайте мастерам календари в разделе «Мастера».
          </p>
        </template>
      </div>
    </template>

    <div v-else class="card">
      <div class="empty-state">
        <div class="empty-title">Бизнес не найден</div>
        <div>Похоже, профиль бизнеса ещё не создан</div>
      </div>
    </div>
  </div>
</template>
