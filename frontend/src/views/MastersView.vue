<script setup>
import { onMounted, reactive, ref } from 'vue'

import { api } from '../api/client'

const masters = ref([])
const loading = ref(true)
const error = ref('')
const saving = ref(false)
const showForm = ref(false)
// null → создание, число → правка существующей
const editingId = ref(null)
// master_id → { link, payload }: сгенерированные ссылки регистрации
const regLinks = reactive({})
const copiedId = ref(null)

const form = reactive({
  full_name: '',
  description: '',
  telegram_id: '',
})

async function load() {
  loading.value = true
  error.value = ''
  try {
    masters.value = await api.get('/api/admin/masters')
  } catch (err) {
    error.value = err.detail ?? err.message
  } finally {
    loading.value = false
  }
}

function openCreate() {
  editingId.value = null
  Object.assign(form, { full_name: '', description: '', telegram_id: '' })
  error.value = ''
  showForm.value = true
}

function openEdit(m) {
  editingId.value = m.id
  Object.assign(form, {
    full_name: m.full_name,
    description: m.description ?? '',
    telegram_id: m.telegram_id === null || m.telegram_id === undefined ? '' : String(m.telegram_id),
  })
  error.value = ''
  showForm.value = true
}

async function submit() {
  if (!form.full_name.trim()) {
    error.value = 'Введите имя мастера'
    return
  }
  if (form.telegram_id.trim() && !/^\d+$/.test(form.telegram_id.trim())) {
    error.value = 'Telegram ID — только цифры'
    return
  }

  saving.value = true
  error.value = ''
  try {
    const tg = form.telegram_id.trim() ? Number(form.telegram_id.trim()) : null
    if (editingId.value === null) {
      await api.post('/api/admin/masters', {
        full_name: form.full_name.trim(),
        description: form.description.trim(),
        telegram_id: tg,
      })
    } else {
      // PATCH не принимает null для telegram_id (null = «не менять»),
      // поэтому пустое поле просто не отправляем
      const patch = {
        full_name: form.full_name.trim(),
        description: form.description.trim(),
      }
      if (tg !== null) patch.telegram_id = tg
      await api.patch(`/api/admin/masters/${editingId.value}`, patch)
    }
    showForm.value = false
    await load()
  } catch (err) {
    error.value = err.detail ?? err.message
  } finally {
    saving.value = false
  }
}

async function toggleActive(m) {
  error.value = ''
  try {
    await api.patch(`/api/admin/masters/${m.id}`, { is_active: !m.is_active })
    await load()
  } catch (err) {
    error.value = err.detail ?? err.message
  }
}

async function makeRegLink(m) {
  error.value = ''
  try {
    const res = await api.post(`/api/admin/masters/${m.id}/registration-link`)
    regLinks[m.id] = res
  } catch (err) {
    error.value = err.detail ?? err.message
  }
}

const creatingCalendarId = ref(null)

async function createCalendar(m) {
  error.value = ''
  creatingCalendarId.value = m.id
  try {
    await api.post(`/api/admin/google-calendar/masters/${m.id}/calendar`)
    await load()
  } catch (err) {
    error.value = err.detail ?? err.message
  } finally {
    creatingCalendarId.value = null
  }
}

async function copyLink(m) {
  const link = regLinks[m.id]?.link
  if (!link) return
  try {
    await navigator.clipboard.writeText(link)
    copiedId.value = m.id
    setTimeout(() => {
      copiedId.value = null
    }, 1500)
  } catch {
    // clipboard недоступен — ссылка всё равно видна и выделяется вручную
  }
}

onMounted(load)
</script>

<template>
  <div>
    <div v-if="error" class="error-box">{{ error }}</div>

    <div class="toolbar">
      <button class="btn btn-primary btn-sm" @click="openCreate">+ Добавить мастера</button>
    </div>

    <form v-if="showForm" class="card" style="margin-bottom: 16px" @submit.prevent="submit">
      <div class="form-grid">
        <div class="field">
          <label for="m-name">Имя</label>
          <input id="m-name" v-model="form.full_name" autocomplete="off" />
        </div>
        <div class="field">
          <label for="m-tg">Telegram ID</label>
          <input
            id="m-tg"
            v-model="form.telegram_id"
            :placeholder="editingId !== null ? 'не менять, если пусто' : 'необязательно'"
            inputmode="numeric"
            autocomplete="off"
          />
        </div>
      </div>
      <div class="field">
        <label for="m-desc">Описание</label>
        <textarea id="m-desc" v-model="form.description" rows="2"></textarea>
      </div>

      <div style="display: flex; gap: 10px">
        <button class="btn btn-primary" type="submit" :disabled="saving">
          {{ saving ? 'Сохраняем…' : editingId === null ? 'Создать' : 'Сохранить' }}
        </button>
        <button class="btn btn-ghost" type="button" @click="showForm = false">Отмена</button>
      </div>
    </form>

    <div v-if="loading" class="muted">Загрузка…</div>

    <div v-else-if="!masters.length" class="card">
      <div class="empty-state">
        <div class="empty-title">Мастеров пока нет</div>
        <div>Добавьте первого мастера — записи и расписание появятся после этого</div>
        <button class="btn btn-primary btn-sm" @click="openCreate">+ Добавить мастера</button>
      </div>
    </div>

    <div v-else class="table-wrap">
      <table class="table">
        <thead>
          <tr>
            <th>Мастер</th>
            <th>Telegram</th>
            <th>Календарь</th>
            <th>Статус</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          <template v-for="m in masters" :key="m.id">
            <tr>
              <td>
                <div>{{ m.full_name }}</div>
                <div v-if="m.description" class="muted" style="font-size: 12px">{{ m.description }}</div>
              </td>
              <td>
                <template v-if="m.telegram_id">
                  <div>{{ m.telegram_id }}</div>
                  <div class="badge badge-green" style="margin-top: 4px">привязан</div>
                </template>
                <template v-else>
                  <div class="muted">не привязан</div>
                  <button class="btn btn-sm" style="margin-top: 4px" @click="makeRegLink(m)">
                    Ссылка для входа
                  </button>
                </template>
              </td>
              <td>
                <span v-if="m.google_calendar_id" class="badge badge-green">создан</span>
                <button
                  v-else
                  class="btn btn-sm"
                  :disabled="creatingCalendarId === m.id"
                  @click="createCalendar(m)"
                >
                  {{ creatingCalendarId === m.id ? 'Создаём…' : 'Создать календарь' }}
                </button>
              </td>
              <td>
                <span class="badge" :class="m.is_active ? 'badge-green' : 'badge-muted'">
                  {{ m.is_active ? 'Активен' : 'Выключен' }}
                </span>
              </td>
              <td style="text-align: right; white-space: nowrap">
                <button class="btn btn-sm" @click="openEdit(m)">Изменить</button>
                <button class="btn btn-sm btn-ghost" @click="toggleActive(m)">
                  {{ m.is_active ? 'Выключить' : 'Включить' }}
                </button>
              </td>
            </tr>
            <tr v-if="regLinks[m.id]">
              <td colspan="5" style="background: rgba(108, 99, 255, 0.05)">
                <div class="link-box" style="margin-top: 0">
                  <span class="link-text">
                    {{ regLinks[m.id].link ?? regLinks[m.id].payload }}
                  </span>
                  <button
                    v-if="regLinks[m.id].link"
                    class="btn btn-sm"
                    type="button"
                    @click="copyLink(m)"
                  >
                    {{ copiedId === m.id ? 'Скопировано' : 'Копировать' }}
                  </button>
                </div>
                <div class="muted" style="font-size: 12px; margin-top: 8px">
                  {{ regLinks[m.id].hint }}
                </div>
              </td>
            </tr>
          </template>
        </tbody>
      </table>
    </div>
  </div>
</template>
