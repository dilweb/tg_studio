<script setup>
import { computed, onMounted, reactive, ref, watch } from 'vue'

import { api } from '../api/client'
import { authStore } from '../store/auth'
import { formatDateTime } from '../utils/format'

const isMaster = computed(() => authStore.me?.role === 'master')

const masters = ref([])
const serviceTypes = ref([])
const items = ref([])
const masterId = ref(null) // id мастера (роль master — только свои записи)
const masterFilter = ref('') // '' = все мастера, число — выбранный мастер
const formMasterId = ref(null) // чей мастер создаётся запись (владелец)

// фильтры поверх загруженного списка + пагинация
const PAGE_SIZE = 15
const page = ref(1)
const serviceFilter = ref('')
const searchText = ref('')

const loading = ref(true)
const error = ref('')
const saving = ref(false)
const cancelingId = ref(null)

// локальная дата (не UTC) — иначе утром «сегодня» сдвигается на вчера
function localISO(d) {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
}
function todayISO() {
  return localISO(new Date())
}
function inDays(n) {
  const d = new Date()
  d.setDate(d.getDate() + n)
  return localISO(d)
}

// дефолт: все мастера, с текущего дня
const range = reactive({ from_date: todayISO(), to_date: inDays(14) })

const form = reactive({
  service_name: '',
  date: todayISO(),
  time: '10:00',
  duration_minutes: 60,
  client_name: '',
  client_phone: '',
})

// дефолт длительности — из настроек мастера (null → 60)
function applyMasterDefault() {
  const mid = isMaster.value ? masterId.value : formMasterId.value
  const m = masters.value.find((mm) => mm.id === mid)
  form.duration_minutes = m?.default_duration_minutes ?? 60
}

async function loadServiceTypes() {
  serviceTypes.value = await api.get('/api/bookings/service-types')
}

async function loadBookings() {
  if (isMaster.value && !masterId.value) return
  if (!isMaster.value && !masters.value.length) return
  error.value = ''
  try {
    const params = new URLSearchParams({
      from_date: `${range.from_date}T00:00:00`,
      to_date: `${range.to_date}T23:59:59`,
    })
    if (isMaster.value) params.set('master_id', masterId.value)
    else if (masterFilter.value) params.set('master_id', masterFilter.value)
    items.value = await api.get(`/api/admin/bookings?${params}`)
    page.value = 1
  } catch (err) {
    error.value = err.detail ?? err.message
  }
}

async function selectMaster(id) {
  masterFilter.value = id === '' ? '' : Number(id)
  await loadBookings()
}

// подсказки типа работы: реестр + то, что реально встречается в записях
const serviceOptions = computed(() => {
  const set = new Set(serviceTypes.value)
  for (const b of items.value) if (b.service_name) set.add(b.service_name)
  return [...set].sort((a, b) => a.localeCompare(b, 'ru'))
})

const filtered = computed(() => {
  let arr = items.value
  if (serviceFilter.value) {
    const s = serviceFilter.value.toLowerCase()
    arr = arr.filter((b) => (b.service_name ?? '').toLowerCase().includes(s))
  }
  const q = searchText.value.trim().toLowerCase()
  if (q) {
    arr = arr.filter((b) =>
      [b.client_name, b.client_phone, b.service_name].some((v) =>
        (v ?? '').toLowerCase().includes(q),
      ),
    )
  }
  return arr
})

const totalPages = computed(() => Math.max(1, Math.ceil(filtered.value.length / PAGE_SIZE)))
const paged = computed(() =>
  filtered.value.slice((page.value - 1) * PAGE_SIZE, page.value * PAGE_SIZE),
)
const pageFrom = computed(() => (filtered.value.length ? (page.value - 1) * PAGE_SIZE + 1 : 0))
const pageTo = computed(() =>
  Math.min(page.value * PAGE_SIZE, filtered.value.length),
)

watch([serviceFilter, searchText], () => {
  page.value = 1
})
// сменил мастера в форме — подтянули его дефолтную длительность
watch(formMasterId, applyMasterDefault)
watch(totalPages, (t) => {
  if (page.value > t) page.value = t
})

async function load() {
  loading.value = true
  error.value = ''
  try {
    await loadServiceTypes()
    if (isMaster.value) {
      masterId.value = authStore.me.master_id
      if (!masterId.value) {
        error.value = 'Профиль мастера не найден'
        return
      }
      applyMasterDefault()
      await loadBookings()
    } else {
      masters.value = await api.get('/api/admin/masters')
      formMasterId.value = masters.value.length ? masters.value[0].id : null
      applyMasterDefault()
      await loadBookings() // сразу все записи, без фильтра по мастеру
    }
  } catch (err) {
    error.value = err.detail ?? err.message
  } finally {
    loading.value = false
  }
}

async function submit() {
  if (!form.client_name.trim()) {
    error.value = 'Введите имя клиента'
    return
  }
  const dur = Number(form.duration_minutes)
  if (!Number.isFinite(dur) || dur < 15) {
    error.value = 'Длительность — минимум 15 минут'
    return
  }
  saving.value = true
  error.value = ''
  try {
    await api.post('/api/admin/bookings', {
      master_id: isMaster.value ? masterId.value : formMasterId.value,
      service_name: form.service_name.trim() || null,
      start_datetime: `${form.date}T${form.time}:00`,
      duration_minutes: Math.round(dur),
      client_name: form.client_name.trim(),
      client_phone: form.client_phone.trim() || null,
    })
    Object.assign(form, { service_name: '', client_name: '', client_phone: '' })
    await loadBookings()
  } catch (err) {
    error.value = err.detail ?? err.message
  } finally {
    saving.value = false
  }
}

async function cancelBooking(b) {
  cancelingId.value = b.event_id
  error.value = ''
  try {
    const mid = b.master_id ?? masterFilter.value ?? ''
    await api.del(`/api/admin/bookings/${encodeURIComponent(b.event_id)}?master_id=${mid}`)
    await loadBookings()
  } catch (err) {
    error.value = err.detail ?? err.message
  } finally {
    cancelingId.value = null
  }
}

onMounted(load)
</script>

<template>
  <div>
    <div v-if="error" class="error-box">{{ error }}</div>

    <div v-if="loading" class="muted">Загрузка…</div>

    <div v-else-if="!isMaster && !masters.length" class="card">
      <div class="empty-state">
        <div class="empty-title">Нет мастеров</div>
        <div>Сначала добавьте мастеров — потом можно будет создавать записи</div>
        <router-link class="btn btn-primary btn-sm" to="/app/masters">Перейти к мастерам</router-link>
      </div>
    </div>

    <template v-else>
      <div class="toolbar">
        <select
          v-if="!isMaster"
          :value="masterFilter"
          @change="selectMaster($event.target.value)"
        >
          <option value="">Все мастера</option>
          <option v-for="m in masters" :key="m.id" :value="m.id">{{ m.full_name }}</option>
        </select>
        <input v-model="range.from_date" type="date" title="Записи с" @change="loadBookings" />
        <input v-model="range.to_date" type="date" title="Записи по" @change="loadBookings" />
      </div>

      <div class="toolbar">
        <select v-model="serviceFilter" title="Тип работы">
          <option value="">Все типы работ</option>
          <option v-for="t in serviceOptions" :key="t" :value="t">{{ t }}</option>
        </select>
        <input
          v-model="searchText"
          type="search"
          placeholder="Поиск: клиент, телефон, тип работы"
          style="flex: 1; min-width: 160px"
        />
      </div>

      <form class="card" style="margin-bottom: 16px" @submit.prevent="submit">
        <div class="form-grid">
          <div v-if="!isMaster" class="field">
            <label for="b-master">Мастер</label>
            <select id="b-master" v-model.number="formMasterId" required>
              <option v-for="m in masters" :key="m.id" :value="m.id">{{ m.full_name }}</option>
            </select>
          </div>
          <div class="field">
            <label for="b-service">Тип работы</label>
            <input
              id="b-service"
              v-model="form.service_name"
              list="service-type-options"
              placeholder="необязательно"
              autocomplete="off"
            />
            <datalist id="service-type-options">
              <option v-for="t in serviceTypes" :key="t" :value="t" />
            </datalist>
          </div>
          <div class="field">
            <label for="b-date">Дата</label>
            <input id="b-date" v-model="form.date" type="date" />
          </div>
          <div class="field">
            <label for="b-time">Время</label>
            <input id="b-time" v-model="form.time" type="time" />
          </div>
          <div class="field">
            <label for="b-duration">Длительность, мин</label>
            <input
              id="b-duration"
              v-model.number="form.duration_minutes"
              type="number"
              min="15"
              step="15"
            />
          </div>
          <div class="field">
            <label for="b-name">Клиент</label>
            <input id="b-name" v-model="form.client_name" autocomplete="off" />
          </div>
          <div class="field">
            <label for="b-phone">Телефон</label>
            <input id="b-phone" v-model="form.client_phone" placeholder="необязательно" autocomplete="off" />
          </div>
        </div>
        <button class="btn btn-primary" type="submit" :disabled="saving">
          {{ saving ? 'Создаём…' : 'Создать запись' }}
        </button>
      </form>

      <div v-if="!filtered.length" class="card">
        <div class="empty-state">
          <div class="empty-title">{{ items.length ? 'Ничего не найдено' : 'Записей нет' }}</div>
          <div>
            {{
              items.length
                ? 'Попробуй изменить фильтры или период'
                : 'На выбранный период записей не найдено'
            }}
          </div>
        </div>
      </div>

      <template v-else>
        <div class="muted" style="font-size: 12px; margin-bottom: 8px">
          Найдено: {{ filtered.length }} · показано {{ pageFrom }}–{{ pageTo }}
        </div>

        <div class="table-wrap">
          <table class="table">
            <thead>
              <tr>
                <th>Когда</th>
                <th v-if="!isMaster">Мастер</th>
                <th>Клиент</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="b in paged" :key="b.event_id">
                <td>
                  <div>{{ formatDateTime(b.starts_at) }}</div>
                  <div class="muted" style="font-size: 12px">до {{ formatDateTime(b.ends_at) }}</div>
                </td>
                <td v-if="!isMaster">{{ b.master_name ?? '—' }}</td>
                <td>
                  <div>{{ b.client_name }}</div>
                  <div v-if="b.service_name" class="muted" style="font-size: 12px">{{ b.service_name }}</div>
                  <div v-if="b.client_phone" class="muted" style="font-size: 12px">{{ b.client_phone }}</div>
                </td>
                <td style="text-align: right; white-space: nowrap">
                  <button
                    class="btn btn-sm btn-ghost"
                    :disabled="cancelingId === b.event_id"
                    @click="cancelBooking(b)"
                  >
                    {{ cancelingId === b.event_id ? 'Отменяем…' : 'Отменить' }}
                  </button>
                </td>
              </tr>
            </tbody>
          </table>
        </div>

        <div v-if="totalPages > 1" class="toolbar" style="margin-top: 12px">
          <button class="btn btn-sm" :disabled="page === 1" @click="page--">← Назад</button>
          <span class="muted" style="align-self: center">Страница {{ page }} из {{ totalPages }}</span>
          <button class="btn btn-sm" :disabled="page === totalPages" @click="page++">Вперёд →</button>
        </div>
      </template>
    </template>
  </div>
</template>
