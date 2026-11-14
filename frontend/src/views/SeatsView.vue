<script setup>
import { computed, onMounted, reactive, ref } from 'vue'

import { api } from '../api/client'
import { authStore } from '../store/auth'

const isOwner = computed(() => authStore.me?.role === 'owner')

const tab = ref('seats') // seats | prices | holidays | discounts | rules

const seats = ref([])
const settings = ref(null)
const hours = ref([])
const priceRules = ref([])
const discountRules = ref([])
const masters = ref([])
const loading = ref(true)
const error = ref('')
const notice = ref('')

// --- места ---
const showForm = ref(false)
const editingId = ref(null)
const form = reactive({ name: '', equipment: '' })

// --- настройки бронирования (вкладка «Правила») ---
const showSettings = ref(false)
const settingsForm = reactive({
  max_seats: '4',
  min_duration_minutes: '60',
  step_minutes: '30',
  buffer_minutes: '0',
  hold_ttl_hours: '24',
  // ступени возврата при отмене мастером: за ≥ hours_before ч до начала → percent %
  refund_tiers: [],
  booking_reminder_minutes: '60',
  payment_reminder_minutes: '120',
})

function addRefundTier() {
  settingsForm.refund_tiers.push({ hours_before: '', percent: '' })
}

function removeRefundTier(index) {
  settingsForm.refund_tiers.splice(index, 1)
}

function refundTiersText(tiers) {
  if (!tiers?.length) return 'отмена мастером — без возврата'
  return tiers.map((t) => `≥${t.hours_before} ч — ${t.percent}%`).join(', ') + ', позже — 0%'
}

// --- часы работы ---
const showHours = ref(false)
const hoursForm = reactive({})
const WEEKDAYS = ['Пн', 'Вт', 'Ср', 'Чт', 'Пт', 'Сб', 'Вс']

// --- ценовые правила ---
const DAY_KINDS = { все: 'Все дни', будни: 'Будни', выходные: 'Выходные', дата: 'Конкретная дата' }
const showRuleForm = ref(false)
const editingRuleId = ref(null)
const ruleForm = reactive({
  seat_id: '',
  name: '',
  day_kind: 'все',
  specific_date: '',
  time_from: '10:00',
  time_to: '22:00',
  hourly_rate: '2000',
  priority: '0',
  is_active: true,
})
// на вкладке «Праздники» форма всегда day_kind=дата
const holidaysMode = computed(() => tab.value === 'holidays')

// --- скидки ---
const showDiscountForm = ref(false)
const editingDiscountId = ref(null)
const discountForm = reactive({
  seat_id: '',
  name: '',
  kind: 'min_hours',
  min_hours: '4',
  percent: '10',
  fixed_amount: '',
  use_percent: true,
  priority: '0',
  is_active: true,
})

// --- калькулятор ---
const quoteForm = reactive({ seat_id: '', start: '', end: '', master_id: '' })
const quoteResult = ref(null)
const quoting = ref(false)

const seatName = (id) => (id == null ? 'Все места' : seats.value.find((s) => s.id === id)?.name ?? `#${id}`)

function fmtMoney(v) {
  return `${Number(v).toLocaleString('ru-RU', { maximumFractionDigits: 2 })} ₸`
}

function fmtTime(t) {
  return String(t).slice(0, 5)
}

async function load() {
  loading.value = true
  error.value = ''
  try {
    const [seatsData, settingsData, hoursData, priceData, discountData] = await Promise.all([
      api.get('/api/seats?include_inactive=true'),
      api.get('/api/coworking/settings'),
      api.get('/api/studio-hours'),
      api.get('/api/price-rules?include_inactive=true'),
      api.get('/api/discount-rules?include_inactive=true'),
    ])
    seats.value = seatsData.seats
    settings.value = settingsData
    hours.value = hoursData.entries
    priceRules.value = priceData.rules
    discountRules.value = discountData.rules
    Object.assign(settingsForm, {
      max_seats: String(settingsData.max_seats),
      min_duration_minutes: String(settingsData.min_duration_minutes),
      step_minutes: String(settingsData.step_minutes),
      buffer_minutes: String(settingsData.buffer_minutes),
      hold_ttl_hours: String(settingsData.hold_ttl_hours),
      refund_tiers: settingsData.refund_tiers.map((t) => ({
        hours_before: String(t.hours_before), percent: String(t.percent),
      })),
      booking_reminder_minutes: String(settingsData.booking_reminder_minutes),
      payment_reminder_minutes: String(settingsData.payment_reminder_minutes),
    })
    hoursFormClear()
    for (const e of hoursData.entries) {
      hoursForm[e.weekday] = { open: e.open_time.slice(0, 5), close: e.close_time.slice(0, 5) }
    }
    if (isOwner.value) {
      try {
        masters.value = await api.get('/api/admin/masters')
      } catch {
        masters.value = []
      }
    }
  } catch (err) {
    error.value = err.detail ?? err.message
  } finally {
    loading.value = false
  }
}

function hoursFormClear() {
  for (let i = 0; i < 7; i++) hoursForm[i] = { open: '10:00', close: '22:00' }
}

// ---------------------------------------------------------------------------
// Места
// ---------------------------------------------------------------------------

function openCreate() {
  editingId.value = null
  Object.assign(form, { name: '', equipment: '' })
  error.value = ''
  showForm.value = true
}

function openEdit(s) {
  editingId.value = s.id
  Object.assign(form, { name: s.name, equipment: s.equipment ?? '' })
  error.value = ''
  showForm.value = true
}

async function submitForm() {
  if (!form.name.trim()) {
    error.value = 'Введите название'
    return
  }
  try {
    const body = { name: form.name.trim(), equipment: form.equipment.trim() || null }
    if (editingId.value === null) {
      await api.post('/api/seats', body)
      notice.value = 'Место добавлено'
    } else {
      await api.patch(`/api/seats/${editingId.value}`, body)
      notice.value = 'Место обновлено'
    }
    showForm.value = false
    await load()
  } catch (err) {
    error.value = err.detail ?? err.message
  }
}

async function toggleActive(s) {
  try {
    if (s.is_active) {
      if (!window.confirm(`Убрать «${s.name}» в архив? Активные брони должны быть отменены.`)) return
      await api.del(`/api/seats/${s.id}`)
      notice.value = 'Место в архиве'
    } else {
      await api.patch(`/api/seats/${s.id}`, { is_active: true })
      notice.value = 'Место снова активно'
    }
    await load()
  } catch (err) {
    error.value = err.detail ?? err.message
  }
}

// ---------------------------------------------------------------------------
// Правила бронирования + часы работы
// ---------------------------------------------------------------------------

async function submitSettings() {
  try {
    await api.patch('/api/coworking/settings', {
      max_seats: Number(settingsForm.max_seats),
      min_duration_minutes: Number(settingsForm.min_duration_minutes),
      step_minutes: Number(settingsForm.step_minutes),
      buffer_minutes: Number(settingsForm.buffer_minutes),
      hold_ttl_hours: Number(settingsForm.hold_ttl_hours),
      refund_tiers: settingsForm.refund_tiers.map((t) => ({
        hours_before: Number(t.hours_before), percent: Number(t.percent),
      })),
      booking_reminder_minutes: Number(settingsForm.booking_reminder_minutes),
      payment_reminder_minutes: Number(settingsForm.payment_reminder_minutes),
    })
    notice.value = 'Настройки сохранены'
    showSettings.value = false
    await load()
  } catch (err) {
    error.value = err.detail ?? err.message
  }
}

async function submitHours() {
  try {
    const entries = []
    for (let wd = 0; wd < 7; wd++) {
      const h = hoursForm[wd]
      entries.push({ weekday: wd, open_time: `${h.open}:00`, close_time: `${h.close}:00` })
    }
    await api.put('/api/studio-hours', { entries })
    notice.value = 'Часы работы сохранены'
    showHours.value = false
    await load()
  } catch (err) {
    error.value = err.detail ?? err.message
  }
}

// ---------------------------------------------------------------------------
// Ценовые правила
// ---------------------------------------------------------------------------

const visiblePriceRules = computed(() =>
  holidaysMode.value ? priceRules.value.filter((r) => r.day_kind === 'дата') : priceRules.value,
)

function openRuleCreate() {
  editingRuleId.value = null
  Object.assign(ruleForm, {
    seat_id: '',
    name: holidaysMode.value ? '' : '',
    day_kind: holidaysMode.value ? 'дата' : 'все',
    specific_date: '',
    time_from: '10:00',
    time_to: '22:00',
    hourly_rate: '2000',
    priority: holidaysMode.value ? '10' : '0',
    is_active: true,
  })
  error.value = ''
  showRuleForm.value = true
}

function openRuleEdit(r) {
  editingRuleId.value = r.id
  Object.assign(ruleForm, {
    seat_id: r.seat_id ?? '',
    name: r.name,
    day_kind: r.day_kind,
    specific_date: r.specific_date ?? '',
    time_from: r.time_from.slice(0, 5),
    time_to: r.time_to.slice(0, 5),
    hourly_rate: String(r.hourly_rate),
    priority: String(r.priority),
    is_active: r.is_active,
  })
  error.value = ''
  showRuleForm.value = true
}

function applyPreset(preset) {
  Object.assign(ruleForm, preset)
  showRuleForm.value = true
}

const PRESETS = [
  { name: 'Буднее утро', day_kind: 'будни', specific_date: '', time_from: '10:00', time_to: '18:00', hourly_rate: '1500', priority: '0' },
  { name: 'Будний вечер', day_kind: 'будни', specific_date: '', time_from: '18:00', time_to: '22:00', hourly_rate: '2500', priority: '0' },
  { name: 'Выходные весь день', day_kind: 'выходные', specific_date: '', time_from: '10:00', time_to: '22:00', hourly_rate: '3000', priority: '0' },
]

async function submitRule() {
  if (!ruleForm.name.trim() || !ruleForm.time_from || !ruleForm.time_to) {
    error.value = 'Заполните название и интервал времени'
    return
  }
  if (ruleForm.day_kind === 'дата' && !ruleForm.specific_date) {
    error.value = 'Укажите дату'
    return
  }
  try {
    const body = {
      seat_id: ruleForm.seat_id ? Number(ruleForm.seat_id) : null,
      name: ruleForm.name.trim(),
      day_kind: ruleForm.day_kind,
      specific_date: ruleForm.day_kind === 'дата' ? ruleForm.specific_date : null,
      time_from: `${ruleForm.time_from}:00`,
      time_to: `${ruleForm.time_to}:00`,
      hourly_rate: Number(ruleForm.hourly_rate),
      priority: Number(ruleForm.priority),
      is_active: ruleForm.is_active,
    }
    if (editingRuleId.value === null) {
      await api.post('/api/price-rules', body)
      notice.value = holidaysMode.value ? 'Особая дата добавлена' : 'Ценовое правило добавлено'
    } else {
      await api.patch(`/api/price-rules/${editingRuleId.value}`, body)
      notice.value = 'Правило обновлено'
    }
    showRuleForm.value = false
    await load()
  } catch (err) {
    const d = err.detail
    error.value = typeof d === 'object' ? d.message ?? JSON.stringify(d) : d ?? err.message
  }
}

async function deleteRule(r) {
  if (!window.confirm(`Удалить правило «${r.name}»? Новые брони по нему считаться не будут.`)) return
  try {
    await api.del(`/api/price-rules/${r.id}`)
    await load()
  } catch (err) {
    error.value = err.detail ?? err.message
  }
}

// ---------------------------------------------------------------------------
// Скидки
// ---------------------------------------------------------------------------

function openDiscountCreate() {
  editingDiscountId.value = null
  Object.assign(discountForm, {
    seat_id: '',
    name: '',
    kind: 'min_hours',
    min_hours: '4',
    percent: '10',
    fixed_amount: '',
    use_percent: true,
    priority: '0',
    is_active: true,
  })
  error.value = ''
  showDiscountForm.value = true
}

function openDiscountEdit(r) {
  editingDiscountId.value = r.id
  Object.assign(discountForm, {
    seat_id: r.seat_id ?? '',
    name: r.name,
    kind: r.kind,
    min_hours: String(r.min_hours ?? '4'),
    percent: r.percent != null ? String(Number(r.percent) * 100) : '10',
    fixed_amount: r.fixed_amount != null ? String(r.fixed_amount) : '',
    use_percent: r.percent != null,
    priority: String(r.priority),
    is_active: r.is_active,
  })
  error.value = ''
  showDiscountForm.value = true
}

async function submitDiscount() {
  if (!discountForm.name.trim()) {
    error.value = 'Введите название'
    return
  }
  try {
    const body = {
      seat_id: discountForm.seat_id ? Number(discountForm.seat_id) : null,
      name: discountForm.name.trim(),
      kind: discountForm.kind,
      min_hours: Number(discountForm.min_hours),
      percent: discountForm.use_percent ? Number(discountForm.percent) / 100 : null,
      fixed_amount: discountForm.use_percent ? null : Number(discountForm.fixed_amount),
      priority: Number(discountForm.priority),
      is_active: discountForm.is_active,
    }
    if (editingDiscountId.value === null) {
      await api.post('/api/discount-rules', body)
      notice.value = 'Скидка добавлена'
    } else {
      await api.patch(`/api/discount-rules/${editingDiscountId.value}`, body)
      notice.value = 'Скидка обновлена'
    }
    showDiscountForm.value = false
    await load()
  } catch (err) {
    const d = err.detail
    error.value = typeof d === 'object' ? JSON.stringify(d) : d ?? err.message
  }
}

async function deleteDiscount(r) {
  if (!window.confirm(`Удалить скидку «${r.name}»?`)) return
  try {
    await api.del(`/api/discount-rules/${r.id}`)
    await load()
  } catch (err) {
    error.value = err.detail ?? err.message
  }
}

// ---------------------------------------------------------------------------
// Калькулятор цены
// ---------------------------------------------------------------------------

async function runQuote() {
  if (!quoteForm.seat_id || !quoteForm.start || !quoteForm.end) {
    error.value = 'Выберите место и интервал для расчёта'
    return
  }
  quoting.value = true
  error.value = ''
  try {
    const body = {
      seat_id: Number(quoteForm.seat_id),
      start: quoteForm.start,
      end: quoteForm.end,
      master_id: quoteForm.master_id ? Number(quoteForm.master_id) : null,
    }
    quoteResult.value = await api.post('/api/pricing/quote', body)
  } catch (err) {
    quoteResult.value = null
    const d = err.detail
    error.value = typeof d === 'object' && d?.text ? d.text : typeof d === 'object' ? d?.message ?? JSON.stringify(d) : d ?? err.message
  } finally {
    quoting.value = false
  }
}

onMounted(() => {
  const d = new Date()
  const p = (n) => String(n).padStart(2, '0')
  quoteForm.start = `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}T12:00`
  quoteForm.end = `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}T14:00`
  load()
})
</script>

<template>
  <div class="page">
    <div class="section-head">
      <h2>Настройка мест</h2>
      <div v-if="isOwner" class="row-actions">
        <button class="btn btn-primary" type="button" @click="openCreate">+ Добавить место</button>
      </div>
    </div>

    <div v-if="error" class="error-box">{{ error }}</div>
    <div v-if="notice" class="success-box">{{ notice }}</div>
    <div v-if="loading" class="muted">Загрузка…</div>

    <template v-else>
      <div class="tabs">
        <button
          v-for="t in [
            ['seats', 'Места'],
            ['prices', 'Цены'],
            ['holidays', 'Праздники и особые даты'],
            ['discounts', 'Скидки'],
            ['rules', 'Правила бронирования'],
          ]"
          :key="t[0]"
          class="btn btn-sm"
          :class="tab === t[0] ? 'btn-primary' : 'btn-ghost'"
          type="button"
          @click="tab = t[0]"
        >{{ t[1] }}</button>
      </div>

      <!-- ======================= МЕСТА ======================= -->
      <template v-if="tab === 'seats'">
        <div class="seat-grid">
          <div
            v-for="s in seats"
            :key="s.id"
            class="card seat-card"
            :class="{ 'seat-inactive': !s.is_active }"
          >
            <div class="seat-name">{{ s.name }} <span v-if="!s.is_active" class="badge badge-muted">Архив</span></div>
            <div class="muted note-line">{{ s.equipment || 'Без описания оснащения' }}</div>
            <div v-if="isOwner" class="row-actions">
              <button class="btn btn-sm btn-ghost" type="button" @click="openEdit(s)">Править</button>
              <button class="btn btn-sm btn-ghost" type="button" @click="toggleActive(s)">
                {{ s.is_active ? 'В архив' : 'Вернуть' }}
              </button>
            </div>
          </div>
        </div>
      </template>

      <!-- ======================= ЦЕНЫ ======================= -->
      <template v-if="tab === 'prices' || tab === 'holidays'">
        <div class="card">
          <div class="section-head">
            <h3>{{ holidaysMode ? 'Праздники и особые даты' : 'Ценовые правила' }}</h3>
            <div v-if="isOwner" class="row-actions">
              <button v-if="!holidaysMode" class="btn btn-sm btn-ghost" type="button" @click="applyPreset(PRESETS[0])">Буднее утро</button>
              <button v-if="!holidaysMode" class="btn btn-sm btn-ghost" type="button" @click="applyPreset(PRESETS[1])">Будний вечер</button>
              <button v-if="!holidaysMode" class="btn btn-sm btn-ghost" type="button" @click="applyPreset(PRESETS[2])">Выходные</button>
              <button class="btn btn-sm btn-primary" type="button" @click="openRuleCreate">
                {{ holidaysMode ? '+ Особая дата' : '+ Правило' }}
              </button>
            </div>
          </div>
          <div v-if="visiblePriceRules.length === 0" class="empty-state">
            <div class="empty-title">{{ holidaysMode ? 'Особых дат пока нет' : 'Ценовых правил пока нет — без них бронь создать нельзя' }}</div>
          </div>
          <div v-else class="table-wrap">
            <table class="table">
              <thead>
                <tr>
                  <th>Название</th><th>Место</th><th>Дни</th><th>Интервал</th>
                  <th>Ставка/час</th><th>Приоритет</th><th>Статус</th><th v-if="isOwner" class="actions-col"></th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="r in visiblePriceRules" :key="r.id">
                  <td>{{ r.name }}</td>
                  <td>{{ seatName(r.seat_id) }}</td>
                  <td>{{ DAY_KINDS[r.day_kind] ?? r.day_kind }}{{ r.day_kind === 'дата' ? ` (${r.specific_date})` : '' }}</td>
                  <td>{{ fmtTime(r.time_from) }}–{{ fmtTime(r.time_to) }}</td>
                  <td>{{ fmtMoney(r.hourly_rate) }}</td>
                  <td>{{ r.priority }}</td>
                  <td><span class="badge" :class="r.is_active ? '' : 'badge-muted'">{{ r.is_active ? 'Активно' : 'Выкл' }}</span></td>
                  <td v-if="isOwner" class="actions-col">
                    <button class="btn btn-sm btn-ghost" type="button" @click="openRuleEdit(r)">Править</button>
                    <button class="btn btn-sm btn-ghost" type="button" @click="deleteRule(r)">✕</button>
                  </td>
                </tr>
              </tbody>
            </table>
          </div>
        </div>

        <!-- калькулятор -->
        <div class="card form-card">
          <h3>Калькулятор: сколько будет стоить бронь</h3>
          <div class="form-grid">
            <div class="field">
              <label>Место</label>
              <select v-model="quoteForm.seat_id">
                <option v-for="s in seats.filter((x) => x.is_active)" :key="s.id" :value="s.id">{{ s.name }}</option>
              </select>
            </div>
            <div v-if="isOwner" class="field">
              <label>Мастер (для резидентских скидок)</label>
              <select v-model="quoteForm.master_id">
                <option value="">— любой —</option>
                <option v-for="m in masters" :key="m.id" :value="m.id">{{ m.full_name }}{{ m.is_resident ? ' (резидент)' : '' }}</option>
              </select>
            </div>
            <div class="field">
              <label>Начало</label>
              <input v-model="quoteForm.start" type="datetime-local" />
            </div>
            <div class="field">
              <label>Конец</label>
              <input v-model="quoteForm.end" type="datetime-local" />
            </div>
          </div>
          <div class="row-actions">
            <button class="btn btn-primary" type="button" :disabled="quoting" @click="runQuote">Рассчитать</button>
          </div>
          <div v-if="quoteResult" class="quote-result">
            <div v-for="(p, i) in quoteResult.parts" :key="i" class="quote-part">
              {{ p.rule_name }} · {{ p.minutes }} мин × {{ fmtMoney(p.hourly_rate) }}/ч = <b>{{ fmtMoney(p.amount) }}</b>
            </div>
            <div class="quote-line">База: <b>{{ fmtMoney(quoteResult.base) }}</b></div>
            <div v-for="(d, i) in quoteResult.discounts" :key="i" class="quote-line">
              Скидка «{{ d.name }}»: −{{ fmtMoney(d.amount) }}
            </div>
            <div class="quote-line quote-total">Итого: <b>{{ fmtMoney(quoteResult.final) }}</b> {{ quoteResult.currency }}</div>
          </div>
        </div>
      </template>

      <!-- ======================= СКИДКИ ======================= -->
      <template v-if="tab === 'discounts'">
        <div class="card">
          <div class="section-head">
            <h3>Скидки</h3>
            <div v-if="isOwner" class="row-actions">
              <button class="btn btn-sm btn-primary" type="button" @click="openDiscountCreate">+ Скидка</button>
            </div>
          </div>
          <div v-if="discountRules.length === 0" class="empty-state">
            <div class="empty-title">Скидок пока нет</div>
          </div>
          <div v-else class="table-wrap">
            <table class="table">
              <thead>
                <tr><th>Название</th><th>Место</th><th>Условие</th><th>Размер</th><th>Приоритет</th><th>Статус</th><th v-if="isOwner" class="actions-col"></th></tr>
              </thead>
              <tbody>
                <tr v-for="r in discountRules" :key="r.id">
                  <td>{{ r.name }}</td>
                  <td>{{ seatName(r.seat_id) }}</td>
                  <td>От {{ r.min_hours }} ч брони</td>
                  <td>{{ r.percent != null ? `${Number(r.percent) * 100}%` : fmtMoney(r.fixed_amount) }}</td>
                  <td>{{ r.priority }}</td>
                  <td><span class="badge" :class="r.is_active ? '' : 'badge-muted'">{{ r.is_active ? 'Активна' : 'Выкл' }}</span></td>
                  <td v-if="isOwner" class="actions-col">
                    <button class="btn btn-sm btn-ghost" type="button" @click="openDiscountEdit(r)">Править</button>
                    <button class="btn btn-sm btn-ghost" type="button" @click="deleteDiscount(r)">✕</button>
                  </td>
                </tr>
              </tbody>
            </table>
          </div>
        </div>
      </template>

      <!-- ======================= ПРАВИЛА БРОНИРОВАНИЯ ======================= -->
      <template v-if="tab === 'rules'">
        <div class="card">
          <div class="section-head">
            <h3>Правила бронирования</h3>
            <div class="row-actions">
              <button class="btn btn-sm" :class="showSettings ? 'btn-primary' : 'btn-ghost'" type="button" @click="showSettings = !showSettings">Править</button>
            </div>
          </div>
          <div v-if="showSettings" class="form-grid">
            <div class="field">
              <label>Макс. число мест</label>
              <input v-model="settingsForm.max_seats" type="number" min="1" max="100" />
            </div>
            <div class="field">
              <label>Мин. длительность, мин</label>
              <input v-model="settingsForm.min_duration_minutes" type="number" min="15" step="15" />
            </div>
            <div class="field">
              <label>Шаг бронирования, мин</label>
              <input v-model="settingsForm.step_minutes" type="number" min="5" step="5" />
            </div>
            <div class="field">
              <label>Буфер между бронями, мин</label>
              <input v-model="settingsForm.buffer_minutes" type="number" min="0" step="5" />
            </div>
            <div class="field">
              <label>Hold (дедлайн оплаты), часов</label>
              <input v-model="settingsForm.hold_ttl_hours" type="number" min="1" max="168" />
            </div>
            <div class="field">
              <label>Напоминание «скоро бронь» (с кнопкой «Подтвердить»), мин до начала</label>
              <input v-model="settingsForm.booking_reminder_minutes" type="number" min="5" max="1440" />
            </div>
            <div class="field">
              <label>Напоминание «оплатите», мин до дедлайна оплаты</label>
              <input v-model="settingsForm.payment_reminder_minutes" type="number" min="5" max="1440" />
            </div>
            <div class="field">
              <label>Возврат при отмене мастером (площадкой — всегда 100%)</label>
              <div v-for="(tier, index) in settingsForm.refund_tiers" :key="index" class="row-actions">
                <span>за ≥</span>
                <input v-model="tier.hours_before" type="number" min="0" max="720" style="width: 6em" />
                <span>ч до начала —</span>
                <input v-model="tier.percent" type="number" min="0" max="100" style="width: 5em" />
                <span>%</span>
                <button class="btn btn-sm btn-ghost" type="button" @click="removeRefundTier(index)">✕</button>
              </div>
              <small class="muted">Позже последней ступени — 0%.</small>
              <button class="btn btn-sm" type="button" @click="addRefundTier">+ Ступень</button>
            </div>
          </div>
          <div v-if="showSettings" class="row-actions">
            <button class="btn btn-primary" type="button" @click="submitSettings">Сохранить</button>
            <button class="btn btn-ghost" type="button" @click="showSettings = false">Закрыть</button>
          </div>
          <div v-else class="muted">
            Мест: {{ settings.max_seats }} · мин. длительность {{ settings.min_duration_minutes }} мин ·
            шаг {{ settings.step_minutes }} мин · буфер {{ settings.buffer_minutes }} мин ·
            hold {{ settings.hold_ttl_hours }} ч · возврат: {{ refundTiersText(settings.refund_tiers) }} ·
            напоминания: за {{ settings.booking_reminder_minutes }} мин до брони,
            за {{ settings.payment_reminder_minutes }} мин до дедлайна оплаты
          </div>
        </div>

        <div class="card form-card">
          <div class="section-head">
            <h3>Часы работы студии</h3>
            <div class="row-actions">
              <button class="btn btn-sm" :class="showHours ? 'btn-primary' : 'btn-ghost'" type="button" @click="showHours = !showHours">Править</button>
            </div>
          </div>
          <div v-if="showHours" class="hours-grid">
            <div v-for="(label, wd) in WEEKDAYS" :key="wd" class="hours-row">
              <span class="hours-day">{{ label }}</span>
              <input v-model="hoursForm[wd].open" type="time" />
              <span class="muted">—</span>
              <input v-model="hoursForm[wd].close" type="time" />
            </div>
            <div class="row-actions">
              <button class="btn btn-primary" type="button" @click="submitHours">Сохранить</button>
              <button class="btn btn-ghost" type="button" @click="showHours = false">Закрыть</button>
            </div>
          </div>
          <div v-else class="muted">
            <span v-for="(label, wd) in WEEKDAYS" :key="wd" class="hours-chip">
              {{ label }} {{ fmtTime(hours.find((h) => h.weekday === wd)?.open_time ?? '10:00') }}–{{ fmtTime(hours.find((h) => h.weekday === wd)?.close_time ?? '22:00') }}
            </span>
          </div>
        </div>
      </template>
    </template>

    <!-- ======================= ФОРМА МЕСТА ======================= -->
    <div v-if="showForm" class="card form-card">
      <h3>{{ editingId === null ? 'Новое место' : 'Правка места' }}</h3>
      <div class="form-grid">
        <div class="field">
          <label>Название</label>
          <input v-model="form.name" type="text" placeholder="Место 1" />
        </div>
        <div class="field">
          <label>Оснащение</label>
          <input v-model="form.equipment" type="text" placeholder="лампа, кресло, стол" />
        </div>
      </div>
      <div class="row-actions">
        <button class="btn btn-primary" type="button" @click="submitForm">Сохранить</button>
        <button class="btn btn-ghost" type="button" @click="showForm = false">Отмена</button>
      </div>
    </div>

    <!-- ======================= ФОРМА ЦЕНОВОГО ПРАВИЛА ======================= -->
    <div v-if="showRuleForm" class="card form-card">
      <h3>{{ editingRuleId === null ? (holidaysMode ? 'Новая особая дата' : 'Новое ценовое правило') : 'Правка правила' }}</h3>
      <div class="form-grid">
        <div class="field">
          <label>Название</label>
          <input v-model="ruleForm.name" type="text" placeholder="Вечер пятницы" />
        </div>
        <div class="field">
          <label>Место (пусто = все места)</label>
          <select v-model="ruleForm.seat_id">
            <option value="">Все места</option>
            <option v-for="s in seats" :key="s.id" :value="s.id">{{ s.name }}</option>
          </select>
        </div>
        <div v-if="!holidaysMode" class="field">
          <label>Дни</label>
          <select v-model="ruleForm.day_kind">
            <option value="все">Все дни</option>
            <option value="будни">Будни</option>
            <option value="выходные">Выходные</option>
            <option value="дата">Конкретная дата</option>
          </select>
        </div>
        <div v-if="ruleForm.day_kind === 'дата'" class="field">
          <label>Дата</label>
          <input v-model="ruleForm.specific_date" type="date" />
        </div>
        <div class="field">
          <label>С</label>
          <input v-model="ruleForm.time_from" type="time" />
        </div>
        <div class="field">
          <label>До</label>
          <input v-model="ruleForm.time_to" type="time" />
        </div>
        <div class="field">
          <label>Ставка за час, ₸</label>
          <input v-model="ruleForm.hourly_rate" type="number" min="1" step="50" />
        </div>
        <div class="field">
          <label>Приоритет (выше = раньше)</label>
          <input v-model="ruleForm.priority" type="number" step="1" />
        </div>
        <div class="field">
          <label class="check-line">
            <input v-model="ruleForm.is_active" type="checkbox" /> Активно
          </label>
        </div>
      </div>
      <div class="row-actions">
        <button class="btn btn-primary" type="button" @click="submitRule">Сохранить</button>
        <button class="btn btn-ghost" type="button" @click="showRuleForm = false">Отмена</button>
      </div>
    </div>

    <!-- ======================= ФОРМА СКИДКИ ======================= -->
    <div v-if="showDiscountForm" class="card form-card">
      <h3>{{ editingDiscountId === null ? 'Новая скидка' : 'Правка скидки' }}</h3>
      <div class="form-grid">
        <div class="field">
          <label>Название</label>
          <input v-model="discountForm.name" type="text" placeholder="Резиденты" />
        </div>
        <div class="field">
          <label>Место (пусто = все места)</label>
          <select v-model="discountForm.seat_id">
            <option value="">Все места</option>
            <option v-for="s in seats" :key="s.id" :value="s.id">{{ s.name }}</option>
          </select>
        </div>
        <div class="field">
          <label>От скольких часов</label>
          <input v-model="discountForm.min_hours" type="number" min="1" max="720" />
        </div>
        <div class="field">
          <label>Размер</label>
          <div class="row-actions">
            <select v-model="discountForm.use_percent">
              <option :value="true">Процент от стоимости</option>
              <option :value="false">Фиксированная сумма, ₸</option>
            </select>
            <input v-if="discountForm.use_percent" v-model="discountForm.percent" type="number" min="0.1" max="100" step="0.5" style="width: 90px" />
            <input v-else v-model="discountForm.fixed_amount" type="number" min="1" step="50" style="width: 110px" />
          </div>
        </div>
        <div class="field">
          <label>Приоритет (выше = раньше)</label>
          <input v-model="discountForm.priority" type="number" step="1" />
        </div>
        <div class="field">
          <label class="check-line">
            <input v-model="discountForm.is_active" type="checkbox" /> Активна
          </label>
        </div>
      </div>
      <div class="row-actions">
        <button class="btn btn-primary" type="button" @click="submitDiscount">Сохранить</button>
        <button class="btn btn-ghost" type="button" @click="showDiscountForm = false">Отмена</button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.tabs {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-bottom: 16px;
}
.seat-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(220px, 1fr));
  gap: 12px;
}
.seat-card {
  display: flex;
  flex-direction: column;
  gap: 6px;
  align-items: flex-start;
}
.seat-inactive {
  opacity: 0.6;
}
.seat-name {
  font-weight: 600;
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
.hours-grid {
  display: flex;
  flex-direction: column;
  gap: 8px;
  margin-bottom: 12px;
}
.hours-row {
  display: flex;
  align-items: center;
  gap: 8px;
}
.hours-day {
  width: 32px;
  font-weight: 600;
}
.hours-chip {
  margin-right: 12px;
  white-space: nowrap;
}
.check-line {
  display: flex;
  align-items: center;
  gap: 8px;
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
.quote-line {
  margin-top: 4px;
}
.quote-total {
  font-size: 16px;
  margin-top: 8px;
}
</style>
