<script setup>
import { computed, onMounted, reactive, ref } from 'vue'

import { api } from '../api/client'
import { authStore } from '../store/auth'

const isOwner = computed(() => authStore.me?.role === 'owner')
// Админ (05) действует за студию: создаёт брони за мастера, отмечает визиты/начало/неявку
const isOwnerOrAdmin = computed(() => ['owner', 'admin'].includes(authStore.me?.role))

const seats = ref([])
const loading = ref(true)
const error = ref('')
const notice = ref('')

// выбранная дата (YYYY-MM-DD) и диапазон календаря — неделя вокруг неё
const todayStr = () => {
  const d = new Date()
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
}
const dateStr = ref(todayStr())

const selectedSeat = ref(null) // null = вся студия
const calendar = ref([]) // entries

const bookings = ref([]) // мои брони (мастер) / все (владелец)

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

// --- форма брони ---
const showForm = ref(false)
const saving = ref(false)
const form = reactive({ seat_id: '', start: '', end: '', master_id: '' })
const masters = ref([])
// расчёт цены перед созданием брони (03)
const quote = ref(null)
const quoting = ref(false)

// --- форма блокировки (владелец) ---
const showBlock = ref(false)
const blockForm = reactive({ seat_id: '', start: '', end: '', reason: '' })

const weekRange = computed(() => {
  const d = new Date(dateStr.value + 'T00:00:00')
  const from = new Date(d)
  from.setDate(d.getDate() - 3)
  const to = new Date(d)
  to.setDate(d.getDate() + 4)
  return { from: toIsoLocal(from), to: toIsoLocal(to) }
})

function toIsoLocal(d) {
  const p = (n) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}T${p(d.getHours())}:${p(d.getMinutes())}:00`
}

function fmt(iso) {
  return new Date(iso).toLocaleString('ru-RU', {
    day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit',
  })
}

function fmtDay(iso) {
  return new Date(iso).toLocaleDateString('ru-RU', { weekday: 'short', day: '2-digit', month: '2-digit' })
}

// группировка записей календаря по дню
const calendarByDay = computed(() => {
  const map = {}
  for (const e of calendar.value) {
    const key = new Date(e.start).toDateString()
    ;(map[key] ??= []).push(e)
  }
  return map
})

async function loadSeats() {
  seats.value = (await api.get('/api/seats')).seats
}

async function loadCalendar() {
  const { from, to } = weekRange.value
  const params = new URLSearchParams({ from, to })
  if (selectedSeat.value) params.set('seat_id', selectedSeat.value)
  calendar.value = (await api.get(`/api/calendar?${params}`)).entries
}

async function loadBookings() {
  const { from, to } = weekRange.value
  bookings.value = (await api.get(`/api/bookings?from=${from}&to=${to}`)).bookings
}

async function load() {
  loading.value = true
  error.value = ''
  try {
    await Promise.all([loadSeats(), loadCalendar(), loadBookings()])
  } catch (err) {
    error.value = err.detail ?? err.message
  } finally {
    loading.value = false
  }
}

async function loadMasters() {
  if (!isOwnerOrAdmin.value || masters.value.length) return
  try {
    masters.value = (await api.get('/api/admin/masters')).masters
  } catch {
    masters.value = []
  }
}

function shiftDate(days) {
  const d = new Date(dateStr.value + 'T00:00:00')
  d.setDate(d.getDate() + days)
  dateStr.value = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
}

function openCreate(seatId = null) {
  const base = dateStr.value
  form.seat_id = seatId ?? (seats.value[0]?.id ?? '')
  form.start = `${base}T12:00`
  form.end = `${base}T14:00`
  form.master_id = ''
  quote.value = null
  error.value = ''
  showForm.value = true
  loadMasters()
}

function fmtMoney(v) {
  return `${Number(v).toLocaleString('ru-RU', { maximumFractionDigits: 2 })} ₸`
}

async function runQuote() {
  if (!form.seat_id || !form.start || !form.end) return
  quoting.value = true
  try {
    const body = { seat_id: Number(form.seat_id), start: form.start, end: form.end }
    if (isOwnerOrAdmin.value && form.master_id) body.master_id = Number(form.master_id)
    quote.value = await api.post('/api/pricing/quote', body)
  } catch (err) {
    quote.value = null
    const d = err.detail
    error.value = typeof d === 'object' && d?.text ? d.text : typeof d === 'object' ? d?.message ?? JSON.stringify(d) : (d ?? err.message)
  } finally {
    quoting.value = false
  }
}

async function submitBooking() {
  if (!form.seat_id || !form.start || !form.end) {
    error.value = 'Выберите место и интервал'
    return
  }
  saving.value = true
  error.value = ''
  try {
    const body = {
      seat_id: Number(form.seat_id),
      start: form.start,
      end: form.end,
    }
    if (isOwnerOrAdmin.value && form.master_id) body.master_id = Number(form.master_id)
    await api.post('/api/bookings', body)
    notice.value = 'Бронь создана (ожидает оплаты)'
    showForm.value = false
    quote.value = null
    await load()
  } catch (err) {
    // 409 с конфликтом — detail объект с conflicts
    const d = err.detail
    error.value = typeof d === 'object' && d?.text ? d.text : (d ?? err.message)
  } finally {
    saving.value = false
  }
}

// --- отметки для админа/владельца (05) ---
const acting = ref(false)

async function confirmBooking(b) {
  acting.value = true
  error.value = ''
  try {
    await api.post(`/api/bookings/${b.id}/confirm`)
    notice.value = 'Бронь подтверждена владельцем'
    await load()
  } catch (err) {
    error.value = err.detail ?? err.message
  } finally {
    acting.value = false
  }
}

async function cancelBooking(b) {
  if (!window.confirm(`Отменить бронь #${b.id} (${b.seat_name}, ${fmt(b.start)})?`)) return
  try {
    await api.post(`/api/bookings/${b.id}/cancel`, { reason: null })
    notice.value = 'Бронь отменена'
    await load()
  } catch (err) {
    error.value = err.detail ?? err.message
  }
}

// Продление на 1 час (шаг продления); для оплаченной брони разница спишется с баланса
async function extendBooking(b) {
  if (!window.confirm(`Продлить бронь #${b.id} на 1 час?`)) return
  error.value = ''
  try {
    await api.post(`/api/bookings/${b.id}/extend`, { hours: 1 })
    notice.value = 'Бронь продлена'
    await load()
  } catch (err) {
    const d = err.detail
    error.value = typeof d === 'object' ? (d.message ?? d.text ?? JSON.stringify(d)) : (d ?? err.message)
  }
}

// --- оплата брони (04) ---
const paying = ref(false)

async function payBooking(b, method) {
  paying.value = true
  error.value = ''
  try {
    const res = await api.post(`/api/bookings/${b.id}/pay`, { method })
    if (method === 'card' && res.payment_url) {
      window.open(res.payment_url, '_blank')
      notice.value = 'Счёт открыт в Kaspi — после оплаты бронь подтвердится автоматически'
    } else {
      notice.value = 'Бронь оплачена и подтверждена'
    }
    await load()
  } catch (err) {
    const d = err.detail
    error.value = typeof d === 'object' ? (d.message ?? JSON.stringify(d)) : (d ?? err.message)
  } finally {
    paying.value = false
  }
}

function openBlock(seatId = null) {
  const base = dateStr.value
  blockForm.seat_id = seatId ?? ''
  blockForm.start = `${base}T22:00`
  blockForm.end = `${base}T23:00`
  blockForm.reason = ''
  error.value = ''
  showBlock.value = true
}

async function submitBlock() {
  if (!blockForm.start || !blockForm.end || !blockForm.reason.trim()) {
    error.value = 'Укажите интервал и причину'
    return
  }
  saving.value = true
  error.value = ''
  try {
    await api.post('/api/blocks', {
      seat_id: blockForm.seat_id ? Number(blockForm.seat_id) : null,
      start: blockForm.start,
      end: blockForm.end,
      reason: blockForm.reason.trim(),
    })
    notice.value = 'Блокировка создана'
    showBlock.value = false
    await load()
  } catch (err) {
    const d = err.detail
    error.value = typeof d === 'object' && d?.text ? d.text : (d ?? err.message)
  } finally {
    saving.value = false
  }
}

async function removeBlock(b) {
  if (!window.confirm('Удалить блокировку?')) return
  try {
    await api.del(`/api/blocks/${b.id}`)
    await load()
  } catch (err) {
    error.value = err.detail ?? err.message
  }
}

onMounted(load)
</script>

<template>
  <div class="page">
    <div class="section-head">
      <h2>Календарь</h2>
      <div class="row-actions">
        <button class="btn btn-primary" type="button" @click="openCreate()">+ Новая бронь</button>
        <button v-if="isOwner" class="btn" type="button" @click="openBlock()">+ Блокировка</button>
      </div>
    </div>

    <div v-if="error" class="error-box">{{ error }}</div>
    <div v-if="notice" class="success-box">{{ notice }}</div>

    <div class="toolbar">
      <button class="btn btn-sm btn-ghost" type="button" @click="shiftDate(-1)">←</button>
      <input v-model="dateStr" type="date" @change="load" />
      <button class="btn btn-sm btn-ghost" type="button" @click="shiftDate(1)">→</button>
      <select v-model="selectedSeat" @change="loadCalendar">
        <option :value="null">Вся студия</option>
        <option v-for="s in seats" :key="s.id" :value="s.id">{{ s.name }}</option>
      </select>
    </div>

    <div v-if="loading" class="muted">Загрузка…</div>

    <template v-else>
      <!-- сетка мест -->
      <div class="seat-grid">
        <div
          v-for="s in seats"
          :key="s.id"
          class="card seat-card"
          :class="{ 'seat-selected': selectedSeat === s.id, 'seat-inactive': !s.is_active }"
          @click="selectedSeat = selectedSeat === s.id ? null : s.id; loadCalendar()"
        >
          <div class="seat-name">{{ s.name }}</div>
          <div class="muted note-line">{{ s.equipment || '—' }}</div>
          <button class="btn btn-sm" type="button" @click.stop="openCreate(s.id)">Забронировать</button>
        </div>
      </div>

      <!-- лента календаря -->
      <div class="card">
        <h3>Лента</h3>
        <div v-if="calendar.length === 0" class="empty-state">
          <div class="empty-title">На этой неделе пусто</div>
        </div>
        <div v-for="(entries, day) in calendarByDay" :key="day" class="cal-day">
          <div class="cal-day-title">{{ fmtDay(entries[0].start) }}</div>
          <div
            v-for="e in entries"
            :key="`${e.kind}-${e.id}`"
            class="cal-entry"
            :class="e.kind === 'booking' ? 'cal-booking' : 'cal-block'"
          >
            <span class="cal-time">{{ fmt(e.start).slice(-5) }}–{{ fmt(e.end).slice(-5) }}</span>
            <span v-if="e.kind === 'booking'">
              {{ e.seat_name }} · {{ e.master_name }} ·
              <b>{{ STATUS_LABELS[e.status] ?? e.status }}</b>
            </span>
            <span v-else-if="e.kind === 'busy'">{{ e.seat_name ?? 'Вся студия' }} · занято</span>
            <span v-else>🔒 {{ e.seat_name ?? 'Вся студия' }} · {{ e.reason }}</span>
            <button
              v-if="e.kind === 'block' && isOwner"
              class="btn btn-sm btn-ghost"
              type="button"
              @click="removeBlock(e)"
            >✕</button>
          </div>
        </div>
      </div>

      <!-- брони -->
      <div class="card">
        <h3>{{ isOwnerOrAdmin ? 'Все брони' : 'Мои брони' }}</h3>
        <div v-if="bookings.length === 0" class="empty-state">
          <div class="empty-title">Броней нет</div>
        </div>
        <div v-else class="table-wrap">
          <table class="table">
            <thead>
              <tr><th>Место</th><th>Начало</th><th>Конец</th><th>Мастер</th><th>Статус</th><th>Цена</th><th>Hold до</th><th>Визит</th><th class="actions-col"></th></tr>
            </thead>
            <tbody>
              <tr v-for="b in bookings" :key="b.id">
                <td>{{ b.seat_name }}</td>
                <td>{{ fmt(b.start) }}</td>
                <td>{{ fmt(b.end) }}</td>
                <td>{{ b.master_name }}</td>
                <td><span class="badge">{{ STATUS_LABELS[b.status] ?? b.status }}</span></td>
                <td>
                  {{ b.price_snapshot ? fmtMoney(b.price_snapshot.final) : '—' }}
                  <span v-if="b.paid_amount > 0" class="muted"> (оплачено {{ fmtMoney(b.paid_amount) }})</span>
                </td>
                <td class="muted">{{ b.hold_deadline ? fmt(b.hold_deadline) : '—' }}</td>
                <td class="muted">
                  <span v-if="b.presence_confirmed_at" class="badge">присутствие ✓</span>
                  <span v-if="b.requires_confirmation && !b.confirmed_by_user_id" class="badge">вне покрытия</span>
                </td>
                <td class="actions-col">
                  <template v-if="b.status === 'ожидает_оплаты'">
                    <button
                      class="btn btn-sm btn-primary"
                      type="button"
                      :disabled="paying"
                      @click="payBooking(b, 'card')"
                    >Оплатить картой</button>
                    <button
                      class="btn btn-sm"
                      type="button"
                      :disabled="paying"
                      @click="payBooking(b, 'balance')"
                    >С баланса</button>
                  </template>
                  <button
                    v-if="['ожидает_оплаты', 'подтверждена', 'начата'].includes(b.status)"
                    class="btn btn-sm"
                    type="button"
                    @click="extendBooking(b)"
                  >Продлить +1 ч</button>
                  <button
                    v-if="['ожидает_оплаты', 'подтверждена'].includes(b.status) && new Date(b.start) > new Date()"
                    class="btn btn-sm btn-ghost"
                    type="button"
                    @click="cancelBooking(b)"
                  >Отменить</button>
                  <template v-if="isOwnerOrAdmin">
                    <button
                      v-if="b.requires_confirmation && !b.confirmed_by_user_id && isOwner"
                      class="btn btn-sm btn-primary"
                      type="button"
                      :disabled="acting"
                      @click="confirmBooking(b)"
                    >Подтвердить (вне покрытия)</button>
                  </template>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>
    </template>

    <!-- форма брони -->
    <div v-if="showForm" class="card form-card">
      <h3>Новая бронь</h3>
      <div class="form-grid">
        <div class="field">
          <label>Место</label>
          <select v-model="form.seat_id">
            <option v-for="s in seats.filter((x) => x.is_active)" :key="s.id" :value="s.id">{{ s.name }}</option>
          </select>
        </div>
        <div v-if="isOwnerOrAdmin" class="field">
          <label>Мастер</label>
          <select v-model="form.master_id">
            <option value="">— любой —</option>
            <option v-for="m in masters" :key="m.id" :value="m.id">{{ m.full_name }}</option>
          </select>
        </div>
        <div class="field">
          <label>Начало</label>
          <input v-model="form.start" type="datetime-local" />
        </div>
        <div class="field">
          <label>Конец</label>
          <input v-model="form.end" type="datetime-local" @change="quote = null" />
        </div>
      </div>
      <div class="row-actions">
        <button class="btn" type="button" :disabled="quoting" @click="runQuote">Показать цену</button>
        <button class="btn btn-primary" type="button" :disabled="saving" @click="submitBooking">Создать</button>
        <button class="btn btn-ghost" type="button" @click="showForm = false">Отмена</button>
      </div>
      <div v-if="quote" class="quote-result">
        <div v-for="(p, i) in quote.parts" :key="i" class="quote-part">
          {{ p.rule_name }} · {{ p.minutes }} мин × {{ fmtMoney(p.hourly_rate) }}/ч = {{ fmtMoney(p.amount) }}
        </div>
        <div v-for="(d, i) in quote.discounts" :key="i">Скидка «{{ d.name }}»: −{{ fmtMoney(d.amount) }}</div>
        <div class="quote-total">Итого: <b>{{ fmtMoney(quote.final) }}</b> {{ quote.currency }}</div>
      </div>
    </div>

    <!-- форма блокировки -->
    <div v-if="showBlock" class="card form-card">
      <h3>Блокировка</h3>
      <div class="form-grid">
        <div class="field">
          <label>Место (пусто = вся студия)</label>
          <select v-model="blockForm.seat_id">
            <option :value="''">Вся студия</option>
            <option v-for="s in seats.filter((x) => x.is_active)" :key="s.id" :value="s.id">{{ s.name }}</option>
          </select>
        </div>
        <div class="field">
          <label>Начало</label>
          <input v-model="blockForm.start" type="datetime-local" />
        </div>
        <div class="field">
          <label>Конец</label>
          <input v-model="blockForm.end" type="datetime-local" />
        </div>
        <div class="field span-2">
          <label>Причина</label>
          <input v-model="blockForm.reason" type="text" placeholder="уборка / ремонт" />
        </div>
      </div>
      <div class="row-actions">
        <button class="btn btn-primary" type="button" :disabled="saving" @click="submitBlock">Создать</button>
        <button class="btn btn-ghost" type="button" @click="showBlock = false">Отмена</button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.seat-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(180px, 1fr));
  gap: 12px;
  margin-bottom: 16px;
}
.seat-card {
  cursor: pointer;
  display: flex;
  flex-direction: column;
  gap: 6px;
  align-items: flex-start;
}
.seat-card .btn {
  margin-top: 4px;
}
.seat-selected {
  outline: 2px solid var(--accent);
}
.seat-inactive {
  opacity: 0.5;
}
.seat-name {
  font-weight: 600;
}
.cal-day {
  margin-bottom: 10px;
}
.cal-day-title {
  font-weight: 600;
  font-size: 13px;
  color: var(--muted);
  margin-bottom: 4px;
  text-transform: capitalize;
}
.cal-entry {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 6px 10px;
  border-radius: 8px;
  margin-bottom: 4px;
  font-size: 13px;
  background: var(--surface);
  border: 1px solid var(--border);
}
.cal-block {
  background: rgba(255, 193, 7, 0.08);
}
.cal-time {
  font-variant-numeric: tabular-nums;
  color: var(--muted);
  white-space: nowrap;
}
.form-card {
  margin-top: 16px;
}
.form-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 12px;
  margin-bottom: 12px;
}
.field.span-2 {
  grid-column: span 2;
}
.quote-result {
  margin-top: 12px;
  border-top: 1px solid var(--border);
  padding-top: 10px;
  font-size: 14px;
}
.quote-part {
  color: var(--muted);
}
.quote-total {
  margin-top: 6px;
  font-size: 15px;
}
</style>
