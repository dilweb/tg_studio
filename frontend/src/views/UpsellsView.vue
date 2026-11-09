<script setup>
import { computed, onMounted, reactive, ref } from 'vue'

import { api } from '../api/client'
import { authStore } from '../store/auth'

// «Апсейлы» (05): админ — свои продажи + свои выплаты; владелец — все апсейлы,
// сводка по админам, начисление доначислений и отметка выплаты.
const isOwner = computed(() => authStore.me?.role === 'owner')

const upsells = ref([])
const payouts = ref([])
const summary = ref([])
const admins = ref([])
const masters = ref([])
const loading = ref(true)
const error = ref('')
const notice = ref('')
const saving = ref(false)

// период отчёта
const today = new Date()
const monthStart = `${today.getFullYear()}-${String(today.getMonth() + 1).padStart(2, '0')}-01T00:00`
const range = reactive({ from: monthStart, to: '' })

// форма апсейла (админ)
const showUpsell = ref(false)
const upsellForm = reactive({ amount: '', master_id: '', buyer_name: '', buyer_phone: '', booking_id: '', note: '' })

// форма доначисления (владелец)
const showPayout = ref(false)
const payoutForm = reactive({ admin_id: '', mode: 'amount', amount: '', percent: '', from: '', to: '', reason: '' })

function fmt(iso) {
  return new Date(iso).toLocaleString('ru-RU', {
    day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit',
  })
}

function fmtMoney(v) {
  return `${Number(v).toLocaleString('ru-RU', { maximumFractionDigits: 2 })} ₸`
}

function toIsoLocal(d) {
  const p = (n) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}T${p(d.getHours())}:${p(d.getMinutes())}:00`
}

async function load() {
  loading.value = true
  error.value = ''
  try {
    const fromIso = new Date(range.from).toISOString()
    const toIso = new Date(range.to || Date.now() + 86400 * 1000).toISOString()
    const [up, po] = await Promise.all([
      api.get(`/api/upsells?from=${fromIso}&to=${toIso}&limit=500`),
      api.get('/api/payouts'),
    ])
    upsells.value = up.upsells
    payouts.value = po.payouts
    if (isOwner.value) {
      const [ad, ms, sm] = await Promise.all([
        api.get('/api/admins'),
        api.get('/api/admin/masters'),
        api.get(`/api/upsells/summary?from=${fromIso}&to=${toIso}`),
      ])
      admins.value = ad.admins
      masters.value = ms
      summary.value = sm
    }
  } catch (err) {
    error.value = err.detail ?? err.message
  } finally {
    loading.value = false
  }
}

async function submitUpsell() {
  if (!upsellForm.amount) {
    error.value = 'Укажите сумму'
    return
  }
  saving.value = true
  error.value = ''
  try {
    await api.post('/api/upsells', {
      amount: Number(upsellForm.amount),
      master_id: upsellForm.master_id ? Number(upsellForm.master_id) : null,
      buyer_name: upsellForm.buyer_name || null,
      buyer_phone: upsellForm.buyer_phone || null,
      booking_id: upsellForm.booking_id ? Number(upsellForm.booking_id) : null,
      note: upsellForm.note || null,
    })
    notice.value = 'Апсейл зафиксирован'
    showUpsell.value = false
    upsellForm.amount = ''
    upsellForm.master_id = ''
    upsellForm.buyer_name = ''
    upsellForm.buyer_phone = ''
    upsellForm.booking_id = ''
    upsellForm.note = ''
    await load()
  } catch (err) {
    error.value = err.detail ?? err.message
  } finally {
    saving.value = false
  }
}

async function submitPayout() {
  if (!payoutForm.admin_id || !payoutForm.reason.trim()) {
    error.value = 'Укажите админа и основание'
    return
  }
  if (payoutForm.mode === 'amount' && !payoutForm.amount) {
    error.value = 'Укажите сумму'
    return
  }
  if (payoutForm.mode === 'percent' && (!payoutForm.percent || !payoutForm.from || !payoutForm.to)) {
    error.value = 'Укажите процент и период'
    return
  }
  saving.value = true
  error.value = ''
  try {
    const body = {
      admin_id: Number(payoutForm.admin_id),
      reason: payoutForm.reason.trim(),
    }
    if (payoutForm.mode === 'amount') {
      body.amount = Number(payoutForm.amount)
    } else {
      body.percent = Number(payoutForm.percent) / 100
      body.from = new Date(payoutForm.from).toISOString()
      body.to = new Date(payoutForm.to).toISOString()
    }
    await api.post('/api/payouts', body)
    notice.value = 'Доначисление создано'
    showPayout.value = false
    await load()
  } catch (err) {
    const d = err.detail
    error.value = typeof d === 'object' ? (d.message ?? JSON.stringify(d)) : (d ?? err.message)
  } finally {
    saving.value = false
  }
}

async function markPaid(p) {
  saving.value = true
  error.value = ''
  try {
    await api.post(`/api/payouts/${p.id}/mark-paid`)
    notice.value = 'Выплата отмечена'
    await load()
  } catch (err) {
    error.value = err.detail ?? err.message
  } finally {
    saving.value = false
  }
}

onMounted(load)
</script>

<template>
  <div class="page">
    <div class="section-head">
      <h2>Апсейлы и выплаты</h2>
      <div class="row-actions">
        <button v-if="!isOwner" class="btn btn-primary" type="button" @click="showUpsell = true">+ Апсейл</button>
        <button v-if="isOwner" class="btn btn-primary" type="button" @click="showPayout = true">+ Начислить</button>
      </div>
    </div>

    <div class="toolbar">
      <label class="muted">Период с</label>
      <input v-model="range.from" type="datetime-local" @change="load" />
      <label class="muted">по</label>
      <input v-model="range.to" type="datetime-local" @change="load" />
    </div>

    <div v-if="error" class="error-box">{{ error }}</div>
    <div v-if="notice" class="success-box">{{ notice }}</div>

    <div v-if="isOwner && summary.length" class="seat-grid">
      <div v-for="s in summary" :key="s.admin_id" class="card seat-card">
        <div class="seat-name">{{ s.admin_name ?? `#${s.admin_id}` }}</div>
        <div class="muted">{{ s.count }} продаж</div>
        <div class="balance-value small">{{ fmtMoney(s.total_amount) }}</div>
      </div>
    </div>

    <div v-if="loading" class="muted">Загрузка…</div>

    <template v-else>
      <div class="card">
        <h3>Апсейлы</h3>
        <div v-if="upsells.length === 0" class="empty-state">
          <div class="empty-title">Апсейлов за период нет</div>
        </div>
        <div v-else class="table-wrap">
          <table class="table">
            <thead>
              <tr><th>Дата</th><th>Админ</th><th>Кому</th><th>Сумма</th><th>Бронь</th><th>Примечание</th></tr>
            </thead>
            <tbody>
              <tr v-for="u in upsells" :key="u.id">
                <td class="muted">{{ fmt(u.created_at) }}</td>
                <td>{{ u.admin_name ?? `#${u.admin_id}` }}</td>
                <td>
                  <template v-if="u.master_id">мастер #{{ u.master_id }}</template>
                  <template v-else>{{ u.buyer_name || '—' }}<span v-if="u.buyer_phone" class="muted"> ({{ u.buyer_phone }})</span></template>
                </td>
                <td>{{ fmtMoney(u.amount) }}</td>
                <td class="muted">{{ u.booking_id ? `#${u.booking_id}` : '—' }}</td>
                <td class="muted">{{ u.note || '—' }}</td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>

      <div class="card">
        <h3>Выплаты (доначисления)</h3>
        <div v-if="payouts.length === 0" class="empty-state">
          <div class="empty-title">Доначислений нет</div>
        </div>
        <div v-else class="table-wrap">
          <table class="table">
            <thead>
              <tr><th>Дата</th><th>Админ</th><th>Сумма</th><th>Статус</th><th>Основание</th><th class="actions-col"></th></tr>
            </thead>
            <tbody>
              <tr v-for="p in payouts" :key="p.id">
                <td class="muted">{{ fmt(p.created_at) }}</td>
                <td>{{ p.admin_name ?? `#${p.admin_id}` }}</td>
                <td>{{ fmtMoney(p.amount) }}</td>
                <td>
                  <span class="badge" :class="{ 'badge-ok': p.status === 'paid' }">
                    {{ p.status === 'paid' ? 'Выплачено' : 'Начислено' }}
                  </span>
                </td>
                <td class="muted">
                  {{ p.reason }}
                  <span v-if="p.percent !== null"> ({{ (p.percent * 100).toFixed(0) }}% от апсейлов)</span>
                </td>
                <td class="actions-col">
                  <button
                    v-if="isOwner && p.status !== 'paid'"
                    class="btn btn-sm btn-primary"
                    type="button"
                    :disabled="saving"
                    @click="markPaid(p)"
                  >Выплачено</button>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>
    </template>

    <div v-if="showUpsell" class="card form-card">
      <h3>Зафиксировать апсейл</h3>
      <div class="form-grid">
        <div class="field">
          <label>Сумма, ₸</label>
          <input v-model="upsellForm.amount" type="number" min="1" step="0.01" />
        </div>
        <div class="field">
          <label>Мастеру (опционально)</label>
          <select v-model="upsellForm.master_id">
            <option value="">— разовый контакт —</option>
            <option v-for="m in masters" :key="m.id" :value="m.id">{{ m.full_name }}</option>
          </select>
        </div>
        <div class="field">
          <label>Имя покупателя</label>
          <input v-model="upsellForm.buyer_name" type="text" />
        </div>
        <div class="field">
          <label>Телефон покупателя</label>
          <input v-model="upsellForm.buyer_phone" type="text" />
        </div>
        <div class="field">
          <label>Бронь (опционально)</label>
          <input v-model="upsellForm.booking_id" type="number" min="1" />
        </div>
        <div class="field span-2">
          <label>Что продано</label>
          <input v-model="upsellForm.note" type="text" placeholder="позиции продажи" />
        </div>
      </div>
      <div class="row-actions">
        <button class="btn btn-primary" type="button" :disabled="saving" @click="submitUpsell">Зафиксировать</button>
        <button class="btn btn-ghost" type="button" @click="showUpsell = false">Отмена</button>
      </div>
    </div>

    <div v-if="showPayout" class="card form-card">
      <h3>Доначисление админу</h3>
      <div class="form-grid">
        <div class="field">
          <label>Администратор</label>
          <select v-model="payoutForm.admin_id">
            <option value="">— выберите —</option>
            <option v-for="a in admins" :key="a.id" :value="a.id">{{ a.full_name }}</option>
          </select>
        </div>
        <div class="field">
          <label>Способ</label>
          <select v-model="payoutForm.mode">
            <option value="amount">Фиксированная сумма</option>
            <option value="percent">% от апсейлов за период</option>
          </select>
        </div>
        <div v-if="payoutForm.mode === 'amount'" class="field">
          <label>Сумма, ₸</label>
          <input v-model="payoutForm.amount" type="number" min="1" step="0.01" />
        </div>
        <template v-else>
          <div class="field">
            <label>Процент, %</label>
            <input v-model="payoutForm.percent" type="number" min="0.01" max="100" step="0.01" />
          </div>
          <div class="field">
            <label>Период с</label>
            <input v-model="payoutForm.from" type="datetime-local" />
          </div>
          <div class="field">
            <label>Период по</label>
            <input v-model="payoutForm.to" type="datetime-local" />
          </div>
        </template>
        <div class="field span-2">
          <label>Основание (обязательно)</label>
          <input v-model="payoutForm.reason" type="text" placeholder="например: 10% от продаж за октябрь" />
        </div>
      </div>
      <div class="row-actions">
        <button class="btn btn-primary" type="button" :disabled="saving" @click="submitPayout">Начислить</button>
        <button class="btn btn-ghost" type="button" @click="showPayout = false">Отмена</button>
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
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.seat-name {
  font-weight: 600;
}
.balance-value.small {
  font-size: 20px;
  font-weight: 700;
  font-variant-numeric: tabular-nums;
}
.toolbar {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 12px;
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
.badge-ok {
  background: rgba(46, 204, 113, 0.15);
}
</style>
