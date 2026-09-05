<script setup>
import { onMounted, reactive, ref } from 'vue'

import { api } from '../api/client'
import { BOOKING_STATUS, SERVICE_TYPE, STATUS_FILTERS } from '../labels'
import { formatDate, formatDateTime, formatMoney } from '../utils/format'

const items = ref([])
const masters = ref([])
const loading = ref(true)
const error = ref('')
// id записи, для которой сейчас идёт запрос start/complete
const actingId = ref(null)

const filters = reactive({
  status: '',
  master_id: '',
  from_date: '',
  to_date: '',
})

async function load() {
  loading.value = true
  error.value = ''
  try {
    const params = new URLSearchParams()
    if (filters.status) params.set('status', filters.status)
    if (filters.master_id) params.set('master_id', filters.master_id)
    if (filters.from_date) params.set('from_date', filters.from_date)
    if (filters.to_date) params.set('to_date', filters.to_date)
    const qs = params.toString()
    items.value = await api.get(`/api/admin/bookings${qs ? `?${qs}` : ''}`)
  } catch (err) {
    error.value = err.detail ?? err.message
  } finally {
    loading.value = false
  }
}

function resetFilters() {
  Object.assign(filters, { status: '', master_id: '', from_date: '', to_date: '' })
  load()
}

function canStart(b) {
  // «Взять в работу» — только для проектных услуг в статусе confirmed
  return b.service_type === 'project' && b.status === 'confirmed'
}

function canComplete(b) {
  return b.status === 'confirmed' || b.status === 'in_progress'
}

async function act(b, action) {
  actingId.value = b.id
  error.value = ''
  try {
    await api.patch(`/api/admin/bookings/${b.id}/${action}`)
    await load()
  } catch (err) {
    error.value = err.detail ?? err.message
  } finally {
    actingId.value = null
  }
}

function statusBadge(status) {
  const s = BOOKING_STATUS[status]
  return s ? `badge-${s.tone}` : 'badge-muted'
}

onMounted(async () => {
  // Мастера нужны для фильтра; их отсутствие не должно ломать список записей
  api
    .get('/api/admin/masters')
    .then((m) => {
      masters.value = m
    })
    .catch(() => {})
  await load()
})
</script>

<template>
  <div>
    <div v-if="error" class="error-box">{{ error }}</div>

    <div class="toolbar">
      <select v-model="filters.status" @change="load">
        <option v-for="f in STATUS_FILTERS" :key="f.value" :value="f.value">{{ f.label }}</option>
      </select>
      <select v-model="filters.master_id" @change="load">
        <option value="">Все мастера</option>
        <option v-for="m in masters" :key="m.id" :value="m.id">{{ m.full_name }}</option>
      </select>
      <input v-model="filters.from_date" type="date" title="Созданы с" @change="load" />
      <input v-model="filters.to_date" type="date" title="Созданы по" @change="load" />
      <button class="btn btn-sm btn-ghost" type="button" @click="resetFilters">Сбросить</button>
    </div>

    <div v-if="loading" class="muted">Загрузка…</div>

    <div v-else-if="!items.length" class="card">
      <div class="empty-state">
        <div class="empty-title">Записей нет</div>
        <div>По выбранным фильтрам ничего не найдено</div>
      </div>
    </div>

    <div v-else class="table-wrap">
      <table class="table">
        <thead>
          <tr>
            <th>Когда</th>
            <th>Клиент</th>
            <th>Услуга</th>
            <th>Мастер</th>
            <th>Сумма</th>
            <th>Статус</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="b in items" :key="b.id">
            <td>
              <template v-if="b.starts_at">
                <div>{{ formatDateTime(b.starts_at) }}</div>
                <div class="muted" style="font-size: 12px">до {{ formatDateTime(b.ends_at) }}</div>
              </template>
              <template v-else-if="b.project_deadline">
                <div>дедлайн {{ formatDate(b.project_deadline) }}</div>
              </template>
              <span v-else class="muted">—</span>
            </td>
            <td>
              <div>{{ b.client_name }}</div>
              <div v-if="b.client_phone" class="muted" style="font-size: 12px">{{ b.client_phone }}</div>
            </td>
            <td>
              <div>{{ b.service_name }}</div>
              <span class="badge badge-muted" style="margin-top: 4px">
                {{ SERVICE_TYPE[b.service_type] ?? b.service_type }}
              </span>
            </td>
            <td>{{ b.master_name }}</td>
            <td style="white-space: nowrap">
              <div>{{ formatMoney(b.total_amount) }} ₸</div>
              <span v-if="b.prepayment_paid" class="badge badge-green" style="margin-top: 4px">
                предоплата
              </span>
            </td>
            <td>
              <span class="badge" :class="statusBadge(b.status)">
                {{ BOOKING_STATUS[b.status]?.label ?? b.status }}
              </span>
            </td>
            <td style="text-align: right; white-space: nowrap">
              <button
                v-if="canStart(b)"
                class="btn btn-sm"
                :disabled="actingId === b.id"
                @click="act(b, 'start')"
              >
                В работу
              </button>
              <button
                v-if="canComplete(b)"
                class="btn btn-sm btn-primary"
                :disabled="actingId === b.id"
                @click="act(b, 'complete')"
              >
                Завершить
              </button>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>
