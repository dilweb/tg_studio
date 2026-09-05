<script setup>
import { computed, onMounted, reactive, ref } from 'vue'

import { api } from '../api/client'

const masters = ref([])
const services = ref([])
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
  service_ids: [],
})

const serviceName = computed(() => {
  const byId = new Map(services.value.map((s) => [s.id, s]))
  return (id) => byId.get(id)?.name ?? `услуга #${id}`
})

async function load() {
  loading.value = true
  error.value = ''
  try {
    // Услуги нужны для чекбоксов формы и названий в списке мастеров
    const [m, s] = await Promise.all([api.get('/api/admin/masters'), api.get('/api/admin/services')])
    masters.value = m
    services.value = s
  } catch (err) {
    error.value = err.detail ?? err.message
  } finally {
    loading.value = false
  }
}

function openCreate() {
  editingId.value = null
  Object.assign(form, { full_name: '', description: '', telegram_id: '', service_ids: [] })
  error.value = ''
  showForm.value = true
}

function openEdit(m) {
  editingId.value = m.id
  Object.assign(form, {
    full_name: m.full_name,
    description: m.description ?? '',
    telegram_id: m.telegram_id === null || m.telegram_id === undefined ? '' : String(m.telegram_id),
    service_ids: [...m.service_ids],
  })
  error.value = ''
  showForm.value = true
}

function toggleService(id) {
  const i = form.service_ids.indexOf(id)
  if (i === -1) form.service_ids.push(id)
  else form.service_ids.splice(i, 1)
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
        service_ids: [...form.service_ids],
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
      await api.put(`/api/admin/masters/${editingId.value}/services`, {
        service_ids: [...form.service_ids],
      })
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

      <div class="field">
        <label>Услуги мастера</label>
        <div v-if="!services.length" class="muted" style="font-size: 13px">
          Сначала добавьте услуги в разделе «Услуги»
        </div>
        <div v-else style="display: flex; flex-direction: column; gap: 8px">
          <label v-for="s in services" :key="s.id" class="checkbox-row">
            <input type="checkbox" :checked="form.service_ids.includes(s.id)" @change="toggleService(s.id)" />
            <span>{{ s.name }}</span>
            <span class="muted">— {{ s.price }} ₸</span>
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
        <div>Добавьте первого мастера и привяжите к нему услуги</div>
        <button class="btn btn-primary btn-sm" @click="openCreate">+ Добавить мастера</button>
      </div>
    </div>

    <div v-else class="table-wrap">
      <table class="table">
        <thead>
          <tr>
            <th>Мастер</th>
            <th>Услуги</th>
            <th>Telegram</th>
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
                <div v-if="m.service_ids.length" class="chips">
                  <span v-for="id in m.service_ids" :key="id" class="chip">{{ serviceName(id) }}</span>
                </div>
                <span v-else class="muted">—</span>
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
