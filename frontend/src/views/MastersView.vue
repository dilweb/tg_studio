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
  default_duration_minutes: '',
  specializations: [], // стили мастера; пусто = универсал (любой стиль)
})
// Справочник стилей — тот же, что в работах (/api/tattoo/options)
const styleOptions = ref([])

async function load() {
  loading.value = true
  error.value = ''
  try {
    const [loaded, options] = await Promise.all([
      api.get('/api/admin/masters'),
      api.get('/api/tattoo/options'),
    ])
    masters.value = loaded
    styleOptions.value = options.styles
  } catch (err) {
    error.value = err.detail ?? err.message
  } finally {
    loading.value = false
  }
}

// «Лайнворк (Linework)» → «Лайнворк», «Графика / Гравюра (Engraving)» → «Графика»
function shortStyle(style) {
  return style.split(' (')[0].split(' / ')[0]
}

function openCreate() {
  editingId.value = null
  Object.assign(form, {
    full_name: '',
    description: '',
    telegram_id: '',
    default_duration_minutes: '',
    specializations: [],
  })
  error.value = ''
  showForm.value = true
}

function openEdit(m) {
  editingId.value = m.id
  Object.assign(form, {
    full_name: m.full_name,
    description: m.description ?? '',
    telegram_id: m.telegram_id === null || m.telegram_id === undefined ? '' : String(m.telegram_id),
    default_duration_minutes: m.default_duration_minutes ? String(m.default_duration_minutes) : '',
    specializations: [...(m.specializations ?? [])],
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
  notice.value = ''
  try {
    const tg = form.telegram_id.trim() ? Number(form.telegram_id.trim()) : null
    const dur = Number(form.default_duration_minutes)
    const duration = form.default_duration_minutes.trim() && dur >= 15 ? Math.round(dur) : null
    if (editingId.value === null) {
      const created = await api.post('/api/admin/masters', {
        full_name: form.full_name.trim(),
        description: form.description.trim(),
        telegram_id: tg,
        default_duration_minutes: duration,
        specializations: [...form.specializations],
      })
      if (created.google_calendar_id) {
        notice.value = `Мастер добавлен, календарь создан. Нажми «Добавить себе» в строке ${created.full_name}, чтобы он появился в твоём Google Calendar.`
      } else {
        notice.value = 'Мастер добавлен.'
      }
    } else {
      // PATCH не принимает null для telegram_id (null = «не менять»),
      // поэтому пустое поле просто не отправляем
      const patch = {
        full_name: form.full_name.trim(),
        description: form.description.trim(),
        // Специализации отправляем всегда: снятые галочки — это «универсал»
        specializations: [...form.specializations],
      }
      if (tg !== null) patch.telegram_id = tg
      if (duration !== null) patch.default_duration_minutes = duration
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
  notice.value = ''
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
// подсказка после успешных действий (создание календаря, ссылка входа)
const notice = ref('')

async function createCalendar(m) {
  error.value = ''
  notice.value = ''
  creatingCalendarId.value = m.id
  try {
    const res = await api.post(`/api/admin/google-calendar/masters/${m.id}/calendar`)
    if (res.share_error) {
      error.value = `Календарь создан, но открыть доступ не удалось: ${res.share_error}`
    } else {
      notice.value = `Календарь создан. Нажми «Добавить себе» в строке ${m.full_name}, чтобы он появился в твоём Google Calendar.`
    }
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
    <div v-else-if="notice" class="success-box">{{ notice }}</div>

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
        <div class="field">
          <label for="m-dur">Длительность записи, мин</label>
          <input
            id="m-dur"
            v-model="form.default_duration_minutes"
            placeholder="60"
            inputmode="numeric"
            autocomplete="off"
          />
        </div>
      </div>
      <div class="field">
        <label for="m-desc">Описание</label>
        <textarea id="m-desc" v-model="form.description" rows="2"></textarea>
      </div>

      <div class="field">
        <label>Специализации (не отметишь — считается универсалом, берёт любые стили)</label>
        <div style="display: flex; flex-wrap: wrap; gap: 6px 14px; margin-top: 4px">
          <label
            v-for="style in styleOptions"
            :key="style"
            style="display: flex; align-items: center; gap: 6px; font-size: 13px"
          >
            <input type="checkbox" :value="style" v-model="form.specializations" />
            <span>{{ shortStyle(style) }}</span>
          </label>
        </div>
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
        <div>Добавьте первого мастера — записи появятся после этого</div>
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
                <div v-if="m.specializations?.length" style="margin-top: 4px; display: flex; flex-wrap: wrap; gap: 4px">
                  <span v-for="s in m.specializations" :key="s" class="badge badge-muted">
                    {{ shortStyle(s) }}
                  </span>
                </div>
                <div v-else class="muted" style="font-size: 12px; margin-top: 4px">универсал</div>
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
                <template v-if="m.google_calendar_id">
                  <div><span class="badge badge-green">создан</span></div>
                  <a
                    class="btn btn-sm"
                    style="margin-top: 4px"
                    :href="m.calendar_add_url"
                    target="_blank"
                    rel="noopener"
                  >
                    Добавить себе
                  </a>
                </template>
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
                  Отправь эту ссылку мастеру — по ней он откроет миниапп и привяжет свой Telegram.
                  Ссылка одноразовая: при генерации новой старая перестаёт работать.
                </div>
              </td>
            </tr>
          </template>
        </tbody>
      </table>
    </div>
  </div>
</template>
