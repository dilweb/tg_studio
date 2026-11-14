<script setup>
import { computed, onMounted, ref } from 'vue'

import { api } from '../api/client'
import { authStore } from '../store/auth'

// Раздел «Админ» (05): текущие брони студии и операционные исключения.
// Ручных отметок посещения нет: присутствие мастер подтверждает кнопкой в напоминании Telegram.
const isOwner = computed(() => authStore.me?.role === 'owner')

const bookings = ref([])
const exceptions = ref([])
const loading = ref(true)
const error = ref('')
const notice = ref('')
const acting = ref(false)

const STATUS_LABELS = {
  ожидает_оплаты: 'Ожидает оплаты',
  подтверждена: 'Подтверждена',
  начата: 'Начата',
  завершена: 'Завершена',
  отменена_мастером: 'Отменена мастером',
  отменена_площадкой: 'Отменена площадкой',
  неявка: 'Неявка',
  истекла_оплата: 'Истекла оплата',
}

const EXCEPTION_LABELS = {
  payment_expired: 'Истёк hold оплаты',
  out_of_coverage_unconfirmed: 'Вне покрытия, не подтверждена',
}

function fmt(iso) {
  return new Date(iso).toLocaleString('ru-RU', {
    day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit',
  })
}

function fmtMoney(v) {
  return `${Number(v).toLocaleString('ru-RU', { maximumFractionDigits: 2 })} ₸`
}

async function load() {
  loading.value = true
  error.value = ''
  try {
    const now = new Date()
    const from = new Date(now.getTime() - 12 * 3600 * 1000)
    const to = new Date(now.getTime() + 48 * 3600 * 1000)
    const params = `from=${from.toISOString()}&to=${to.toISOString()}`
    const [b, ex] = await Promise.all([
      api.get(`/api/bookings?${params}`),
      api.get('/api/ops/exceptions'),
    ])
    bookings.value = b.bookings
    exceptions.value = ex.exceptions
  } catch (err) {
    error.value = err.detail ?? err.message
  } finally {
    loading.value = false
  }
}

async function confirmBooking(b) {
  acting.value = true
  error.value = ''
  try {
    await api.post(`/api/bookings/${b.id}/confirm`)
    notice.value = 'Бронь подтверждена'
    await load()
  } catch (err) {
    error.value = err.detail ?? err.message
  } finally {
    acting.value = false
  }
}

onMounted(load)
</script>

<template>
  <div class="page">
    <div class="section-head">
      <h2>Администраторская</h2>
      <div class="row-actions">
        <button class="btn" type="button" @click="load">Обновить</button>
      </div>
    </div>

    <div v-if="error" class="error-box">{{ error }}</div>
    <div v-if="notice" class="success-box">{{ notice }}</div>

    <div v-if="loading" class="muted">Загрузка…</div>

    <template v-else>
      <div class="card">
        <h3>Исключения</h3>
        <div v-if="exceptions.length === 0" class="empty-state">
          <div class="empty-title">Исключений нет</div>
        </div>
        <div v-else class="table-wrap">
          <table class="table">
            <thead>
              <tr><th>Тип</th><th>Бронь</th><th>Место</th><th>Мастер</th><th>Начало</th><th>Статус</th></tr>
            </thead>
            <tbody>
              <tr v-for="x in exceptions" :key="`${x.kind}-${x.booking_id}`">
                <td><span class="badge">{{ EXCEPTION_LABELS[x.kind] ?? x.kind }}</span></td>
                <td>#{{ x.booking_id }}</td>
                <td>{{ x.seat_name ?? '—' }}</td>
                <td>{{ x.master_name ?? '—' }}</td>
                <td>{{ fmt(x.start) }}</td>
                <td class="muted">{{ STATUS_LABELS[x.status] ?? x.status }}</td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>

      <div class="card">
        <h3>Текущие брони (сегодня ±)</h3>
        <div v-if="bookings.length === 0" class="empty-state">
          <div class="empty-title">Броней нет</div>
        </div>
        <div v-else class="table-wrap">
          <table class="table">
            <thead>
              <tr><th>Место</th><th>Начало</th><th>Конец</th><th>Мастер</th><th>Статус</th><th>Цена</th><th>Присутствие</th><th class="actions-col"></th></tr>
            </thead>
            <tbody>
              <tr v-for="b in bookings" :key="b.id">
                <td>{{ b.seat_name }}</td>
                <td>{{ fmt(b.start) }}</td>
                <td>{{ fmt(b.end) }}</td>
                <td>{{ b.master_name }}</td>
                <td><span class="badge">{{ STATUS_LABELS[b.status] ?? b.status }}</span></td>
                <td>{{ b.price_snapshot ? fmtMoney(b.price_snapshot.final) : '—' }}</td>
                <td class="muted">
                  {{ b.presence_confirmed_at ? `✓ ${fmt(b.presence_confirmed_at).slice(-5)}` : '—' }}
                </td>
                <td class="actions-col">
                  <button
                    v-if="b.requires_confirmation && !b.confirmed_by_user_id && isOwner"
                    class="btn btn-sm btn-primary"
                    type="button"
                    :disabled="acting"
                    @click="confirmBooking(b)"
                  >Подтвердить</button>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>
    </template>
  </div>
</template>
