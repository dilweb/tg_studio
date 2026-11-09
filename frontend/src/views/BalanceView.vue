<script setup>
import { computed, onMounted, ref } from 'vue'

import { api } from '../api/client'
import { authStore } from '../store/auth'

const isOwner = computed(() => authStore.me?.role === 'owner')
// Админ (05) видит балансы мастеров и выписку обзорно, но не пополняет/не корректирует
const isOwnerOrAdmin = computed(() => ['owner', 'admin'].includes(authStore.me?.role))

const balance = ref(null)
const entries = ref([])
const masters = ref([])
const loading = ref(true)
const error = ref('')

// пополнение / корректировка (владелец)
const showTopup = ref(false)
const topupForm = ref({ master_id: '', amount: '', note: '' })
const showAdjust = ref(false)
const adjustForm = ref({ master_id: '', amount: '', reason: '' })
const saving = ref(false)

const KIND_LABELS = {
  topup: 'Пополнение',
  charge: 'Списание',
  refund: 'Возврат',
  adjust: 'Корректировка',
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
    if (isOwnerOrAdmin.value) {
      const [led, ms] = await Promise.all([
        api.get('/api/ledger?limit=200'),
        api.get('/api/admin/masters'),
      ])
      entries.value = led.entries
      masters.value = ms
    } else {
      const [bal, led] = await Promise.all([
        api.get('/api/me/balance'),
        api.get('/api/me/ledger?limit=200'),
      ])
      balance.value = bal
      entries.value = led.entries
    }
  } catch (err) {
    error.value = err.detail ?? err.message
  } finally {
    loading.value = false
  }
}

async function loadOwnerBalance(masterId) {
  if (!masterId) return null
  try {
    return await api.get(`/api/masters/${masterId}/balance`)
  } catch {
    return null
  }
}

const ownerBalances = ref({})

async function submitTopup() {
  if (!topupForm.value.master_id || !topupForm.value.amount) {
    error.value = 'Выберите мастера и сумму'
    return
  }
  saving.value = true
  error.value = ''
  try {
    await api.post(`/api/masters/${topupForm.value.master_id}/topup`, {
      amount: Number(topupForm.value.amount),
      note: topupForm.value.note || null,
    })
    showTopup.value = false
    topupForm.value = { master_id: '', amount: '', note: '' }
    await load()
  } catch (err) {
    error.value = err.detail ?? err.message
  } finally {
    saving.value = false
  }
}

async function submitAdjust() {
  if (!adjustForm.value.master_id || !adjustForm.value.amount || !adjustForm.value.reason.trim()) {
    error.value = 'Укажите мастера, сумму и основание'
    return
  }
  saving.value = true
  error.value = ''
  try {
    await api.post(`/api/masters/${adjustForm.value.master_id}/ledger/adjust`, {
      amount: Number(adjustForm.value.amount),
      reason: adjustForm.value.reason.trim(),
    })
    showAdjust.value = false
    adjustForm.value = { master_id: '', amount: '', reason: '' }
    await load()
  } catch (err) {
    error.value = err.detail ?? err.message
  } finally {
    saving.value = false
  }
}

onMounted(async () => {
  await load()
  if (isOwner.value) {
    for (const m of masters.value) {
      const b = await loadOwnerBalance(m.id)
      if (b) ownerBalances.value[m.id] = b.balance
    }
  }
})
</script>

<template>
  <div class="page">
    <div class="section-head">
      <h2>Баланс</h2>
      <div v-if="isOwner" class="row-actions">
        <button class="btn" type="button" @click="showTopup = true; showAdjust = false">+ Пополнить</button>
        <button class="btn" type="button" @click="showAdjust = true; showTopup = false">Корректировка</button>
      </div>
    </div>

    <div v-if="error" class="error-box">{{ error }}</div>

    <div v-if="!isOwner && balance" class="card balance-card">
      <div class="balance-label">Мой остаток</div>
      <div class="balance-value" :class="{ negative: balance.balance < 0 }">{{ fmtMoney(balance.balance) }}</div>
    </div>

    <div v-if="isOwnerOrAdmin && masters.length" class="seat-grid">
      <div v-for="m in masters" :key="m.id" class="card seat-card">
        <div class="seat-name">{{ m.full_name }}</div>
        <div class="balance-value small">
          {{ ownerBalances[m.id] !== undefined ? fmtMoney(ownerBalances[m.id]) : '…' }}
        </div>
      </div>
    </div>

    <div v-if="loading" class="muted">Загрузка…</div>

    <div v-else class="card">
      <h3>Выписка</h3>
      <div v-if="entries.length === 0" class="empty-state">
        <div class="empty-title">Проводок пока нет</div>
      </div>
      <div v-else class="table-wrap">
        <table class="table">
          <thead>
            <tr>
              <th>Дата</th><th v-if="isOwnerOrAdmin">Мастер</th><th>Тип</th><th>Сумма</th><th>Бронь</th><th>Примечание</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="e in entries" :key="e.id">
              <td class="muted">{{ fmt(e.created_at) }}</td>
              <td v-if="isOwnerOrAdmin">{{ e.master_name ?? `#${e.master_id}` }}</td>
              <td><span class="badge">{{ KIND_LABELS[e.kind] ?? e.kind }}</span></td>
              <td :class="{ negative: e.amount < 0 }">{{ e.amount >= 0 ? '+' : '' }}{{ fmtMoney(e.amount) }}</td>
              <td class="muted">{{ e.booking_id ? `#${e.booking_id}` : '—' }}</td>
              <td class="muted">{{ e.note || '—' }}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>

    <div v-if="showTopup" class="card form-card">
      <h3>Пополнение баланса (наличные/перевод)</h3>
      <div class="form-grid">
        <div class="field">
          <label>Мастер</label>
          <select v-model="topupForm.master_id">
            <option value="">— выберите —</option>
            <option v-for="m in masters" :key="m.id" :value="m.id">{{ m.full_name }}</option>
          </select>
        </div>
        <div class="field">
          <label>Сумма, ₸</label>
          <input v-model="topupForm.amount" type="number" min="1" step="0.01" />
        </div>
        <div class="field span-2">
          <label>Примечание</label>
          <input v-model="topupForm.note" type="text" placeholder="например: наличные за bono" />
        </div>
      </div>
      <div class="row-actions">
        <button class="btn btn-primary" type="button" :disabled="saving" @click="submitTopup">Зачислить</button>
        <button class="btn btn-ghost" type="button" @click="showTopup = false">Отмена</button>
      </div>
    </div>

    <div v-if="showAdjust" class="card form-card">
      <h3>Корректировка баланса</h3>
      <div class="form-grid">
        <div class="field">
          <label>Мастер</label>
          <select v-model="adjustForm.master_id">
            <option value="">— выберите —</option>
            <option v-for="m in masters" :key="m.id" :value="m.id">{{ m.full_name }}</option>
          </select>
        </div>
        <div class="field">
          <label>Сумма, ₸ (+/−)</label>
          <input v-model="adjustForm.amount" type="number" step="0.01" />
        </div>
        <div class="field span-2">
          <label>Основание (обязательно)</label>
          <input v-model="adjustForm.reason" type="text" placeholder="почему корректируем" />
        </div>
      </div>
      <div class="row-actions">
        <button class="btn btn-primary" type="button" :disabled="saving" @click="submitAdjust">Применить</button>
        <button class="btn btn-ghost" type="button" @click="showAdjust = false">Отмена</button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.balance-card {
  margin-bottom: 16px;
}
.balance-label {
  color: var(--muted);
  font-size: 13px;
}
.balance-value {
  font-size: 32px;
  font-weight: 700;
  font-variant-numeric: tabular-nums;
}
.balance-value.small {
  font-size: 20px;
}
.negative {
  color: #d33;
}
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
</style>
