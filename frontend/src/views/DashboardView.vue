<script setup>
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'

import { api } from '../api/client'

const data = ref(null)
const period = ref('day')
const periods = [['day', 'День'], ['week', 'Неделя'], ['month', 'Месяц']]
const periodWord = { day: 'сегодня', week: 'за неделю', month: 'за месяц' }
const loading = ref(true)
const error = ref('')
let timer

const stats = computed(() => data.value ? [
  { label: 'Выручка ' + periodWord[data.value.period], value: money(data.value.revenue.gross), hint: `После возвратов: ${money(data.value.revenue.net)}` },
  { label: 'Поступления', value: money(data.value.money.receipts), hint: 'Фактические деньги, не списания с баланса' },
  { label: 'Брони ' + periodWord[data.value.period], value: data.value.bookings_created, hint: `${data.value.cancellations} отмен` },
  { label: 'Загрузка мест', value: `${Math.round(data.value.usage.percent)}%`, hint: `${data.value.usage.occupied_hours.toFixed(1)} из ${data.value.usage.available_hours.toFixed(1)} ч` },
  { label: 'На балансах мастеров', value: money(data.value.master_balances), hint: 'Неиспользованные средства' },
  { label: 'Апсейлы', value: money(data.value.upsells.amount), hint: 'Продажи администраторов за сегодня' },
] : [])

function money(value) {
  return `${Number(value || 0).toLocaleString('ru-RU', { maximumFractionDigits: 2 })} ₸`
}
function fmt(value) {
  return value ? new Date(value).toLocaleString('ru-RU', { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' }) : '—'
}
function clock(value) {
  return value ? new Date(value).toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit' }) : '—'
}
const exceptionLabels = { payment_expired: 'Истёк hold', out_of_coverage_unconfirmed: 'Вне покрытия' }
const segmentLabels = { morning: 'Утро', evening: 'Вечер', weekend: 'Выходные' }

async function load(silent = false) {
  if (!silent) loading.value = true
  try {
    data.value = await api.get(`/api/dashboard?period=${period.value}`)
    error.value = ''
  } catch (err) {
    error.value = err.detail ?? err.message
  } finally {
    loading.value = false
  }
}
onMounted(() => {
  load()
  timer = setInterval(() => load(true), 60_000)
})
onBeforeUnmount(() => clearInterval(timer))
</script>

<template>
  <div class="page dashboard">
    <div class="section-head">
      <div><h2>Студия сейчас</h2><p v-if="data" class="muted">Обновлено {{ clock(data.generated_at) }} · автоматически каждую минуту</p></div>
      <div class="period-tabs">
        <button v-for="[key, label] in periods" :key="key" class="btn" :class="{ active: period === key }" type="button" @click="period = key; load()">{{ label }}</button>
        <button class="btn" type="button" :disabled="loading" @click="load()">Обновить</button>
      </div>
    </div>
    <div v-if="error" class="error-box">{{ error }}</div>
    <div v-if="loading && !data" class="empty-state">Собираем показатели…</div>
    <template v-if="data">
      <div class="stats-grid">
        <article v-for="stat in stats" :key="stat.label" class="card stat-card">
          <div class="muted">{{ stat.label }}</div><strong>{{ stat.value }}</strong><small>{{ stat.hint }}</small>
        </article>
      </div>
      <div class="dashboard-grid">
        <section class="card">
          <div class="section-head"><h3>Места</h3><router-link to="/app/calendar">Календарь →</router-link></div>
          <div class="seat-list">
            <div v-for="seat in data.seats_now" :key="seat.seat_id" class="seat-row">
              <span class="seat-dot" :class="seat.status"></span><strong>{{ seat.seat_name }}</strong>
              <span>{{ seat.status === 'occupied' ? (seat.master_name || 'Занято') : seat.status === 'blocked' ? 'Закрыто' : 'Свободно' }}</span>
              <small class="muted">{{ seat.status === 'occupied' ? `до ${clock(seat.until)}` : `Ближайшие 4 ч: ${seat.upcoming}` }}</small>
            </div>
          </div>
          <div class="segments">
            <span v-for="part in data.usage.segments" :key="part.segment">{{ segmentLabels[part.segment] }}: <b>{{ part.hours.toFixed(1) }} ч</b></span>
          </div>
        </section>
        <section class="card">
          <div class="section-head"><h3>Сейчас и скоро</h3><span class="badge">{{ data.onsite.length }} идёт</span></div>
          <div v-if="!data.onsite.length && !data.upcoming.length" class="empty-state">Броней нет</div>
          <div v-for="booking in data.onsite" :key="`on-${booking.booking_id}`" class="booking-row">
            <span class="badge live">Идёт</span><div><b>{{ booking.master_name }}</b><small>{{ booking.seat_name }}{{ booking.presence_confirmed ? ' · присутствие ✓' : ' · не подтвердил' }}</small></div><span>до {{ clock(booking.end) }}</span>
          </div>
          <div v-for="booking in data.upcoming" :key="booking.booking_id" class="booking-row">
            <time>{{ clock(booking.start) }}</time><div><b>{{ booking.master_name }}</b><small>{{ booking.seat_name }}</small></div><span class="muted">{{ booking.status }}</span>
          </div>
        </section>
        <section class="card">
          <div class="section-head"><h3>Апсейлы админов</h3><router-link to="/app/upsells">Подробнее →</router-link></div>
          <div v-if="!data.upsells.admins.length" class="empty-state">Продаж пока нет</div>
          <div v-for="admin in data.upsells.admins" :key="admin.admin_id" class="simple-row"><span>{{ admin.admin_name }} <small>{{ admin.count }} прод.</small></span><b>{{ money(admin.amount) }}</b></div>
        </section>
        <section class="card alerts">
          <div class="section-head"><h3>Требуют внимания</h3><router-link to="/app/stock">Склад →</router-link></div>
          <div v-if="!data.low_stock.length && !data.exceptions.length" class="empty-state">Всё спокойно</div>
          <div v-for="product in data.low_stock" :key="`s-${product.id}`" class="alert-row"><span>Заканчивается <b>{{ product.name }}</b></span><small>{{ product.quantity }} {{ product.unit }} · минимум {{ product.min_quantity }}</small></div>
          <div v-for="item in data.exceptions" :key="`${item.kind}-${item.booking_id}`" class="alert-row"><span>{{ exceptionLabels[item.kind] || item.kind }} · {{ item.master_name }}</span><small>{{ item.seat_name }} · {{ fmt(item.start) }}</small></div>
        </section>
      </div>
    </template>
  </div>
</template>

<style scoped>
.period-tabs { display: flex; gap: 6px; }
.period-tabs .btn.active { background: var(--primary, #2563eb); color: #fff; }
.section-head p { margin: 4px 0 0; font-size: 12px; }
.stats-grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 12px; margin-bottom: 16px; }
.stat-card { display: flex; flex-direction: column; gap: 8px; }
.stat-card strong { font-size: 25px; font-variant-numeric: tabular-nums; }
.stat-card small { color: var(--muted); }
.dashboard-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }
a { color: var(--accent); font-size: 13px; text-decoration: none; }
.seat-row { display: grid; grid-template-columns: 10px 1fr 1fr auto; gap: 10px; align-items: center; padding: 13px 0; border-bottom: 1px solid var(--border); }
.seat-dot { width: 8px; height: 8px; border-radius: 50%; background: #3fa97b; }
.seat-dot.occupied { background: #e3a53c; }
.segments { display: flex; flex-wrap: wrap; gap: 18px; margin-top: 16px; color: var(--muted); font-size: 12px; }
.booking-row { display: flex; align-items: center; gap: 12px; padding: 10px 0; border-bottom: 1px solid var(--border); }
.booking-row > div { flex: 1; display: flex; flex-direction: column; gap: 3px; }
.booking-row small { color: var(--muted); }
.booking-row time { min-width: 44px; font-weight: 700; }
.booking-row > span:last-child { font-size: 12px; }
.badge.live { color: #258d60; background: #e3f6ed; }
.simple-row, .alert-row { display: flex; justify-content: space-between; gap: 10px; padding: 10px 0; border-bottom: 1px solid var(--border); }
.simple-row small, .alert-row small { color: var(--muted); }
.alert-row { flex-direction: column; gap: 4px; }
@media (max-width: 1000px) { .stats-grid { grid-template-columns: repeat(2, 1fr); } }
@media (max-width: 700px) { .dashboard-grid { grid-template-columns: 1fr; } .seat-row { grid-template-columns: 10px 1fr 1fr; } .seat-row small { grid-column: 2 / -1; } }
@media (max-width: 500px) { .stats-grid { grid-template-columns: 1fr; } }
</style>
