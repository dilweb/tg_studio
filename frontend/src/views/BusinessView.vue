<script setup>
import { computed, onMounted, reactive, ref } from 'vue'

import { api } from '../api/client'

const info = ref(null)
const loading = ref(true)
const error = ref('')

// ── Профиль бизнеса: название, описание, телефон ─────────────────────────────
const editingInfo = ref(false)
const infoBusy = ref(false)
const infoSaved = ref(false)
const infoForm = reactive({ name: '', description: '', phone: '' })

function openEditInfo() {
  infoForm.name = info.value.name
  infoForm.description = info.value.description ?? ''
  infoForm.phone = info.value.phone ?? ''
  infoSaved.value = false
  editingInfo.value = true
}

async function saveInfo() {
  if (!infoForm.name.trim()) {
    error.value = 'Введите название студии'
    return
  }
  infoBusy.value = true
  infoSaved.value = false
  error.value = ''
  try {
    info.value = await api.put('/api/admin/business', {
      name: infoForm.name.trim(),
      description: infoForm.description.trim(),
      phone: infoForm.phone.trim() || null,
    })
    editingInfo.value = false
    infoSaved.value = true
    setTimeout(() => (infoSaved.value = false), 3000)
  } catch (err) {
    error.value = err.detail ?? err.message
  } finally {
    infoBusy.value = false
  }
}

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
          <span style="display: flex; gap: 8px; align-items: center">
            <span v-if="infoSaved" class="badge badge-green">Сохранено</span>
            <span class="badge" :class="info.is_active ? 'badge-green' : 'badge-muted'">
              {{ info.is_active ? 'Активен' : 'Выключен' }}
            </span>
            <button class="btn btn-sm" @click="editingInfo ? (editingInfo = false) : openEditInfo()">
              {{ editingInfo ? 'Отмена' : 'Изменить' }}
            </button>
          </span>
        </div>

        <form v-if="editingInfo" @submit.prevent="saveInfo">
          <div class="field">
            <label for="b-name">Название</label>
            <input id="b-name" v-model="infoForm.name" autocomplete="off" />
          </div>
          <div class="field">
            <label for="b-desc">Описание студии</label>
            <textarea id="b-desc" v-model="infoForm.description" rows="5"></textarea>
          </div>
          <div class="field" style="max-width: 280px">
            <label for="b-phone">Телефон</label>
            <input id="b-phone" v-model="infoForm.phone" autocomplete="off" />
          </div>
          <button class="btn btn-primary" type="submit" :disabled="infoBusy">
            {{ infoBusy ? 'Сохраняем…' : 'Сохранить' }}
          </button>
        </form>

        <template v-else>
          <div v-if="info.description" style="margin-bottom: 12px; white-space: pre-line">
            {{ info.description }}
          </div>
          <div v-else class="muted" style="margin-bottom: 12px; font-size: 13px">
            Описание не заполнено
          </div>
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
        </template>
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
