<script setup>
import { onMounted, reactive, ref } from 'vue'

import { api, apiFetchBlob } from '../api/client'

const tabs = [
  { id: 'revenue', title: 'Выручка' }, { id: 'usage', title: 'Загрузка' },
  { id: 'masters', title: 'Мастера' }, { id: 'money', title: 'Деньги' },
  { id: 'upsells', title: 'Апсейлы' },
]
const today = new Date()
const month = new Date(today.getFullYear(), today.getMonth(), 1)
function localInput(value) {
  const p = (n) => String(n).padStart(2, '0')
  return `${value.getFullYear()}-${p(value.getMonth() + 1)}-${p(value.getDate())}T${p(value.getHours())}:${p(value.getMinutes())}`
}
const range = reactive({ from: localInput(month), to: localInput(new Date(today.getFullYear(), today.getMonth(), today.getDate() + 1)) })
const active = ref('revenue')
const data = ref(null)
const loading = ref(true)
const exporting = ref(false)
const error = ref('')
const segmentLabels = { morning: 'Утро', evening: 'Вечер', weekend: 'Выходные' }
const money = (v) => `${Number(v || 0).toLocaleString('ru-RU', { maximumFractionDigits: 2 })} ₸`
const number = (v) => Number(v || 0).toLocaleString('ru-RU', { maximumFractionDigits: 2 })
const fmt = (v) => v ? new Date(v).toLocaleString('ru-RU', { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' }) : '—'

async function load() {
  if (!range.from || !range.to) return
  loading.value = true
  error.value = ''
  try {
    const query = `from=${encodeURIComponent(new Date(range.from).toISOString())}&to=${encodeURIComponent(new Date(range.to).toISOString())}`
    data.value = await api.get(`/api/reports/${active.value}?${query}`)
  } catch (err) {
    data.value = null
    error.value = err.detail ?? err.message
  } finally { loading.value = false }
}
function selectTab(tab) { active.value = tab; load() }
async function download() {
  exporting.value = true
  error.value = ''
  try {
    const query = `from=${encodeURIComponent(new Date(range.from).toISOString())}&to=${encodeURIComponent(new Date(range.to).toISOString())}`
    const blob = await apiFetchBlob(`/api/reports/revenue.xlsx?${query}`)
    const url = URL.createObjectURL(blob)
    const link = document.createElement('a')
    link.href = url
    link.download = `revenue_${range.from.slice(0, 10)}_${range.to.slice(0, 10)}.xlsx`
    link.click()
    URL.revokeObjectURL(url)
  } catch (err) { error.value = err.detail ?? err.message } finally { exporting.value = false }
}
onMounted(load)
</script>

<template>
  <div class="page reports">
    <div class="section-head"><h2>Отчёты</h2><button class="btn btn-primary" type="button" :disabled="exporting || loading" @click="download">{{ exporting ? 'Готовим…' : 'Скачать Excel' }}</button></div>
    <div class="toolbar"><label class="muted">Период с</label><input v-model="range.from" type="datetime-local" /><label class="muted">по</label><input v-model="range.to" type="datetime-local" /><button class="btn" type="button" @click="load">Показать</button></div>
    <div class="report-tabs"><button v-for="tab in tabs" :key="tab.id" type="button" class="tab" :class="{ active: active === tab.id }" @click="selectTab(tab.id)">{{ tab.title }}</button></div>
    <div v-if="error" class="error-box">{{ error }}</div><div v-if="loading" class="empty-state">Считаем показатели…</div>
    <template v-else-if="data">
      <template v-if="active === 'revenue'">
        <div class="totals"><div class="card"><span>Выручка</span><b>{{ money(data.gross) }}</b></div><div class="card"><span>Возвраты</span><b>−{{ money(data.refunds) }}</b></div><div class="card"><span>Итого после возвратов</span><b>{{ money(data.net) }}</b></div></div>
        <section class="card"><h3>По дням</h3><div class="table-wrap"><table class="table"><thead><tr><th>Дата</th><th>Брони</th><th>Продажи</th><th>Выручка</th><th>Возвраты</th><th>Итого</th><th>Часы</th></tr></thead><tbody><tr v-for="row in data.by_day" :key="row.date"><td>{{ new Date(`${row.date}T12:00`).toLocaleDateString('ru-RU') }}</td><td>{{ money(row.booking_revenue) }}</td><td>{{ money(row.sales_revenue) }}</td><td><b>{{ money(row.gross) }}</b></td><td>−{{ money(row.refunds) }}</td><td>{{ money(row.net) }}</td><td>{{ number(row.hours) }}</td></tr></tbody></table></div><div v-if="!data.by_day.length" class="empty-state">Нет оплаченных операций в периоде</div></section>
        <div class="two-columns"><section class="card"><h3>По местам</h3><div v-for="row in data.by_seat" :key="row.seat_id" class="simple-row"><span>{{ row.seat_name }} <small>{{ number(row.hours) }} ч</small></span><b>{{ money(row.revenue) }}</b></div></section><section class="card"><h3>Время</h3><div v-for="row in data.segments" :key="row.segment" class="simple-row"><span>{{ segmentLabels[row.segment] }} <small>{{ number(row.percent) }}% занятого времени</small></span><b>{{ number(row.hours) }} ч</b></div></section></div>
      </template>
      <template v-if="active === 'usage'">
        <div class="totals"><div class="card"><span>Доступно</span><b>{{ number(data.available_hours) }} ч</b></div><div class="card"><span>Занято</span><b>{{ number(data.occupied_hours) }} ч</b></div><div class="card"><span>Загрузка</span><b>{{ number(data.percent) }}%</b></div></div>
        <section class="card"><div class="table-wrap"><table class="table"><thead><tr><th>Место</th><th>Доступно</th><th>Занято</th><th>Блокировки</th><th>Свободно</th><th>Загрузка</th></tr></thead><tbody><tr v-for="row in data.seats" :key="row.seat_id"><td><b>{{ row.seat_name }}</b></td><td>{{ number(row.available_hours) }} ч</td><td>{{ number(row.occupied_hours) }} ч</td><td>{{ number(row.blocked_hours) }} ч</td><td>{{ number(row.free_hours) }} ч</td><td><div class="meter"><i :style="{ width: `${Math.min(row.percent, 100)}%` }"></i></div>{{ number(row.percent) }}%</td></tr></tbody></table></div></section>
      </template>
      <template v-if="active === 'masters'">
        <div class="totals"><div class="card"><span>Новые</span><b>{{ data.new }}</b></div><div class="card"><span>Активные в периоде</span><b>{{ data.active }}</b></div><div class="card"><span>Присутствие</span><b>{{ data.presence_confirmed }} из {{ data.presence_expected }}</b><small>Подтвердили кнопкой в напоминании; не подтвердил ≠ не пришёл</small></div></div>
        <section class="card"><div class="table-wrap"><table class="table"><thead><tr><th>Мастер</th><th>Брони</th><th>Часы</th><th>Брони, ₸</th><th>Покупки, ₸</th><th>Выручка</th><th>Баланс</th><th>Присутствие</th></tr></thead><tbody><tr v-for="row in data.masters" :key="row.master_id"><td><b>{{ row.master_name }}</b> <span v-if="row.is_new" class="badge">Новый</span></td><td>{{ row.bookings }}</td><td>{{ number(row.hours) }}</td><td>{{ money(row.booking_revenue) }}</td><td>{{ money(row.sales_revenue) }}</td><td><b>{{ money(row.revenue) }}</b></td><td>{{ money(row.balance) }}</td><td>{{ row.presence_expected ? `${row.presence_confirmed} из ${row.presence_expected}` : "—" }}</td></tr></tbody></table></div></section>
      </template>
      <template v-if="active === 'money'">
        <div class="totals"><div class="card"><span>Поступления</span><b>{{ money(data.receipts) }}</b><small>Оплаченные счета, без внутренних списаний</small></div><div class="card"><span>Возвраты</span><b>−{{ money(data.refunds) }}</b><small>Удержания по броням: {{ money(data.booking_holds) }}</small></div><div class="card"><span>Балансы мастеров</span><b>{{ money(data.total_balance) }}</b><small>Текущий остаток по журналу</small></div></div>
        <section class="card"><h3>Поступления</h3><div class="table-wrap"><table class="table"><thead><tr><th>Дата</th><th>Тип</th><th>Мастер</th><th>Сумма</th><th>Провайдер</th><th>ID / внешний ID</th></tr></thead><tbody><tr v-for="row in data.payments" :key="row.payment_id"><td>{{ fmt(row.date) }}</td><td>{{ row.type === 'sale' ? 'Продажа' : row.type === 'booking' ? 'Бронь' : 'Пополнение' }}</td><td>{{ row.master_name || '—' }}</td><td>{{ money(row.amount) }}</td><td>{{ row.method }}</td><td>#{{ row.payment_id }}<small v-if="row.external_id"> · {{ row.external_id }}</small></td></tr></tbody></table></div><div v-if="!data.payments.length" class="empty-state">Нет поступлений</div></section>
        <section class="card"><h3>Возвраты и удержания</h3><div class="table-wrap"><table class="table"><thead><tr><th>Дата</th><th>Мастер</th><th>Возврат</th><th>Удержание</th><th>Бронь / продажа</th></tr></thead><tbody><tr v-for="(row, index) in data.refund_rows" :key="index"><td>{{ fmt(row.date) }}</td><td>{{ row.master_name || 'Гость' }}</td><td>{{ money(row.amount) }}</td><td>{{ money(row.held) }}</td><td>{{ row.booking_id ? `Бронь #${row.booking_id}` : `Продажа #${row.sale_id}` }}</td></tr></tbody></table></div></section>
      </template>
      <template v-if="active === 'upsells'">
        <section class="card"><h3>Продажи и доначисления администраторам</h3><div class="table-wrap"><table class="table"><thead><tr><th>Администратор</th><th>Продажи</th><th>Товары</th><th>Другие апсейлы</th><th>Мастерам</th><th>Клиентам</th><th>Количество</th><th>Начислено</th><th>Выплачено в периоде</th></tr></thead><tbody><tr v-for="row in data.admins" :key="row.admin_id"><td><b>{{ row.admin_name }}</b></td><td>{{ money(row.amount) }}</td><td>{{ money(row.product_amount) }}</td><td>{{ money(row.manual_amount) }}</td><td>{{ money(row.master_amount) }}</td><td>{{ money(row.client_amount) }}</td><td>{{ row.count }}</td><td>{{ money(row.payout_accrued) }}</td><td>{{ money(row.payout_paid) }}</td></tr></tbody></table></div><div class="total-line">Всего апсейлов: <b>{{ money(data.amount) }}</b> (мастерам {{ money(data.master_amount) }}, клиентам {{ money(data.client_amount) }})</div></section>
      </template>
    </template>
  </div>
</template>

<style scoped>
.reports { min-width: 0; }
.toolbar { flex-wrap: wrap; }
.report-tabs { display: flex; gap: 4px; border-bottom: 1px solid var(--border); margin-bottom: 16px; overflow-x: auto; }
.tab { border: 0; border-bottom: 2px solid transparent; padding: 10px 16px; background: transparent; color: var(--muted); cursor: pointer; white-space: nowrap; }
.tab.active { color: var(--accent); border-bottom-color: var(--accent); font-weight: 600; }
.totals { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 12px; margin-bottom: 16px; }
.totals .card { display: flex; flex-direction: column; gap: 8px; }
.totals span, .totals small { color: var(--muted); }
.totals b { font-size: 23px; }
.card { margin-bottom: 16px; }
.two-columns { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }
.simple-row { display: flex; justify-content: space-between; gap: 10px; padding: 10px 0; border-bottom: 1px solid var(--border); }
.simple-row small { display: block; color: var(--muted); margin-top: 3px; }
.meter { display: inline-block; vertical-align: middle; width: 80px; height: 6px; border-radius: 4px; margin-right: 8px; background: var(--border); overflow: hidden; }
.meter i { display: block; height: 100%; background: var(--accent); }
.total-line { text-align: right; padding-top: 14px; }
@media (max-width: 700px) { .totals, .two-columns { grid-template-columns: 1fr; } }
</style>
