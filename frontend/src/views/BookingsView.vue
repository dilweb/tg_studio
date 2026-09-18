<script setup>
import { computed, onMounted, reactive, ref } from 'vue'

import { api } from '../api/client'
import { authStore } from '../store/auth'
import { formatDateTime } from '../utils/format'

const isMaster = computed(() => authStore.me?.role === 'master')

const masters = ref([])
const services = ref([])
const items = ref([])
const masterId = ref(null)

const loading = ref(true)
const error = ref('')
const saving = ref(false)
const cancelingId = ref(null)

function todayISO() {
  return new Date().toISOString().slice(0, 10)
}
function inDays(n) {
  const d = new Date()
  d.setDate(d.getDate() + n)
  return d.toISOString().slice(0, 10)
}

const range = reactive({ from_date: todayISO(), to_date: inDays(14) })

const form = reactive({
  service_id: '',
  date: todayISO(),
  time: '10:00',
  client_name: '',
  client_phone: '',
})

async function loadServices() {
  services.value = masterId.value ? await api.get(`/api/slots/masters/${masterId.value}/services`) : []
}

async function loadBookings() {
  if (!masterId.value) return
  error.value = ''
  try {
    const params = new URLSearchParams({
      master_id: masterId.value,
      from_date: `${range.from_date}T00:00:00`,
      to_date: `${range.to_date}T23:59:59`,
    })
    items.value = await api.get(`/api/admin/bookings?${params}`)
  } catch (err) {
    error.value = err.detail ?? err.message
  }
}

async function selectMaster(id) {
  masterId.value = Number(id)
  await loadServices()
  await loadBookings()
}

async function load() {
  loading.value = true
  error.value = ''
  try {
    if (isMaster.value) {
      masterId.value = authStore.me.master_id
      if (!masterId.value) {
        error.value = 'Профиль мастера не найден'
        return
      }
      await loadServices()
      await loadBookings()
    } else {
      masters.value = await api.get('/api/admin/masters')
      if (masters.value.length) await selectMaster(masters.value[0].id)
    }
  } catch (err) {
    error.value = err.detail ?? err.message
  } finally {
    loading.value = false
  }
}

async function submit() {
  if (!form.service_id) {
    error.value = 'Выберите услугу'
    return
  }
  if (!form.client_name.trim()) {
    error.value = 'Введите имя клиента'
    return
  }
  saving.value = true
  error.value = ''
  try {
    await api.post('/api/admin/bookings', {
      master_id: masterId.value,
      service_id: Number(form.service_id),
      start_datetime: `${form.date}T${form.time}:00`,
      client_name: form.client_name.trim(),
      client_phone: form.client_phone.trim() || null,
    })
    Object.assign(form, { service_id: '', client_name: '', client_phone: '' })
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
    await api.del(`/api/admin/bookings/${encodeURIComponent(b.event_id)}?master_id=${masterId.value}`)
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
        <select v-if="!isMaster" :value="masterId" @change="selectMaster($event.target.value)">
          <option v-for="m in masters" :key="m.id" :value="m.id">{{ m.full_name }}</option>
        </select>
        <input v-model="range.from_date" type="date" title="Записи с" @change="loadBookings" />
        <input v-model="range.to_date" type="date" title="Записи по" @change="loadBookings" />
      </div>

      <form class="card" style="margin-bottom: 16px" @submit.prevent="submit">
        <div v-if="!services.length" class="muted" style="font-size: 13px">
          У этого мастера пока нет услуг — добавьте их в разделе «Услуги»
        </div>
        <div v-else class="form-grid">
          <div class="field">
            <label for="b-service">Услуга</label>
            <select id="b-service" v-model="form.service_id">
              <option value="" disabled>Выберите услугу</option>
              <option v-for="s in services" :key="s.id" :value="s.id">{{ s.name }} — {{ s.price }} ₸</option>
            </select>
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
            <label for="b-name">Клиент</label>
            <input id="b-name" v-model="form.client_name" autocomplete="off" />
          </div>
          <div class="field">
            <label for="b-phone">Телефон</label>
            <input id="b-phone" v-model="form.client_phone" placeholder="необязательно" autocomplete="off" />
          </div>
        </div>
        <button v-if="services.length" class="btn btn-primary" type="submit" :disabled="saving">
          {{ saving ? 'Создаём…' : 'Создать запись' }}
        </button>
      </form>

      <div v-if="!items.length" class="card">
        <div class="empty-state">
          <div class="empty-title">Записей нет</div>
          <div>На выбранный период записей не найдено</div>
        </div>
      </div>

      <div v-else class="table-wrap">
        <table class="table">
          <thead>
            <tr>
              <th>Когда</th>
              <th>Клиент</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="b in items" :key="b.event_id">
              <td>
                <div>{{ formatDateTime(b.starts_at) }}</div>
                <div class="muted" style="font-size: 12px">до {{ formatDateTime(b.ends_at) }}</div>
              </td>
              <td>
                <div>{{ b.client_name }}</div>
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
    </template>
  </div>
</template>
