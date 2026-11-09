<script setup>
import { computed, onMounted, reactive, ref } from 'vue'

import { api } from '../api/client'
import { authStore } from '../store/auth'

// «Склад» коворкинга (06): товары, закупка, обеспечение площадки, ревизия, журнал.
const isMaster = computed(() => authStore.me?.role === 'master')

const products = ref([])
const loading = ref(true)
const error = ref('')
const notice = ref('')
const saving = ref(false)

const lowOnly = ref(false)
const showInactive = ref(false)

const showForm = ref(false)
const form = reactive({
  name: '', category: 'Расходники', unit: 'шт', quantity: '0',
  min_quantity: '', purchase_price: '', sale_price: '', note: '',
})

const action = ref(null) // 'purchase' | 'consume' | 'adjust'
const actionProduct = ref(null)
const actionForm = reactive({ qty: '', unit_cost: '', reason: '', new_quantity: '', booking_id: '' })

const historyProduct = ref(null)
const movements = ref([])

const KIND_LABELS = {
  init: 'Начальный остаток', purchase: 'Закупка', sale: 'Продажа',
  consume: 'Обеспечение', refund: 'Возврат', adjust: 'Ревизия', use: 'Списание',
}

function fmtQty(v) {
  return Number(v).toLocaleString('ru-RU', { maximumFractionDigits: 2 })
}

function fmtMoney(v) {
  return v === null || v === undefined ? '—' : `${Number(v).toLocaleString('ru-RU')} ₸`
}

async function load() {
  loading.value = true
  error.value = ''
  try {
    const params = new URLSearchParams()
    if (lowOnly.value) params.set('low_only', 'true')
    if (showInactive.value) params.set('include_inactive', 'true')
    products.value = (await api.get(`/api/products?${params}`)).products
  } catch (err) {
    error.value = err.detail ?? err.message
  } finally {
    loading.value = false
  }
}

async function submitProduct() {
  if (!form.name.trim()) {
    error.value = 'Укажите название'
    return
  }
  saving.value = true
  error.value = ''
  try {
    await api.post('/api/products', {
      name: form.name.trim(),
      category: form.category,
      unit: form.unit,
      quantity: Number(form.quantity) || 0,
      min_quantity: form.min_quantity ? Number(form.min_quantity) : null,
      purchase_price: form.purchase_price ? Number(form.purchase_price) : null,
      sale_price: form.sale_price ? Number(form.sale_price) : null,
      note: form.note || null,
    })
    notice.value = 'Товар добавлен'
    showForm.value = false
    form.name = ''
    form.quantity = '0'
    form.min_quantity = ''
    form.purchase_price = ''
    form.sale_price = ''
    form.note = ''
    await load()
  } catch (err) {
    error.value = err.detail ?? err.message
  } finally {
    saving.value = false
  }
}

function openAction(type, p) {
  action.value = type
  actionProduct.value = p
  actionForm.qty = ''
  actionForm.unit_cost = ''
  actionForm.reason = ''
  actionForm.new_quantity = ''
  actionForm.booking_id = ''
  error.value = ''
}

async function submitAction() {
  saving.value = true
  error.value = ''
  try {
    const pid = actionProduct.value.id
    if (action.value === 'purchase') {
      await api.post('/api/stock/purchase', {
        supply_id: pid,
        qty: Number(actionForm.qty),
        unit_cost: actionForm.unit_cost ? Number(actionForm.unit_cost) : null,
        note: actionForm.reason || null,
      })
    } else if (action.value === 'consume') {
      await api.post('/api/stock/consume', {
        supply_id: pid,
        qty: Number(actionForm.qty),
        reason: actionForm.reason,
        booking_id: actionForm.booking_id ? Number(actionForm.booking_id) : null,
      })
    } else {
      await api.post('/api/stock/adjust', {
        supply_id: pid,
        new_quantity: Number(actionForm.new_quantity),
        reason: actionForm.reason,
      })
    }
    notice.value = 'Движение записано'
    action.value = null
    await load()
  } catch (err) {
    const d = err.detail
    error.value = typeof d === 'object' ? (d.message ?? JSON.stringify(d)) : (d ?? err.message)
  } finally {
    saving.value = false
  }
}

async function showHistory(p) {
  historyProduct.value = p
  movements.value = []
  try {
    movements.value = (await api.get(`/api/stock/movements?supply_id=${p.id}`)).movements
  } catch (err) {
    error.value = err.detail ?? err.message
  }
}

function fmtTime(iso) {
  return new Date(iso).toLocaleString('ru-RU', {
    day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit',
  })
}

onMounted(load)
</script>

<template>
  <div class="page">
    <div class="section-head">
      <h2>Склад</h2>
      <div class="row-actions">
        <button v-if="!isMaster" class="btn btn-primary" type="button" @click="showForm = true">+ Товар</button>
      </div>
    </div>

    <div class="toolbar">
      <label><input v-model="lowOnly" type="checkbox" @change="load" /> только низкие остатки</label>
      <label v-if="!isMaster"><input v-model="showInactive" type="checkbox" @change="load" /> показать архив</label>
    </div>

    <div v-if="error" class="error-box">{{ error }}</div>
    <div v-if="notice" class="success-box">{{ notice }}</div>

    <div v-if="loading" class="muted">Загрузка…</div>

    <div v-else class="card">
      <div v-if="products.length === 0" class="empty-state">
        <div class="empty-title">Товаров нет</div>
      </div>
      <div v-else class="table-wrap">
        <table class="table">
          <thead>
            <tr>
              <th>Товар</th><th>Категория</th><th>Остаток</th><th>Закупка</th><th>Продажа</th>
              <th v-if="!isMaster" class="actions-col"></th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="p in products" :key="p.id" :class="{ 'row-low': p.low }">
              <td>{{ p.name }}</td>
              <td class="muted">{{ p.category }}</td>
              <td>
                <strong>{{ fmtQty(p.quantity) }} {{ p.unit }}</strong>
                <span v-if="p.low" class="badge badge-low">мало</span>
              </td>
              <td class="muted">{{ fmtMoney(p.purchase_price) }}</td>
              <td class="muted">{{ fmtMoney(p.sale_price) }}</td>
              <td v-if="!isMaster" class="actions-col">
                <button class="btn btn-sm" type="button" @click="openAction('purchase', p)">Закупка</button>
                <button class="btn btn-sm btn-ghost" type="button" @click="openAction('consume', p)">Расход</button>
                <button class="btn btn-sm btn-ghost" type="button" @click="openAction('adjust', p)">Ревизия</button>
                <button class="btn btn-sm btn-ghost" type="button" @click="showHistory(p)">Журнал</button>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>

    <div v-if="showForm" class="card form-card">
      <h3>Новый товар</h3>
      <div class="form-grid">
        <div class="field"><label>Название</label><input v-model="form.name" type="text" /></div>
        <div class="field"><label>Категория</label><input v-model="form.category" type="text" /></div>
        <div class="field"><label>Единица</label><input v-model="form.unit" type="text" /></div>
        <div class="field"><label>Начальный остаток</label><input v-model="form.quantity" type="number" min="0" /></div>
        <div class="field"><label>Порог</label><input v-model="form.min_quantity" type="number" min="0" /></div>
        <div class="field"><label>Закупочная цена, ₸</label><input v-model="form.purchase_price" type="number" min="0" /></div>
        <div class="field"><label>Цена продажи, ₸</label><input v-model="form.sale_price" type="number" min="0" /></div>
        <div class="field"><label>Примечание</label><input v-model="form.note" type="text" /></div>
      </div>
      <div class="row-actions">
        <button class="btn btn-primary" type="button" :disabled="saving" @click="submitProduct">Добавить</button>
        <button class="btn btn-ghost" type="button" @click="showForm = false">Отмена</button>
      </div>
    </div>

    <div v-if="action" class="card form-card">
      <h3>
        {{ action === 'purchase' ? 'Закупка' : action === 'consume' ? 'Обеспечение площадки' : 'Ревизия' }}:
        {{ actionProduct.name }}
      </h3>
      <div class="form-grid">
        <div v-if="action !== 'adjust'" class="field">
          <label>Количество</label><input v-model="actionForm.qty" type="number" min="0.01" step="0.01" />
        </div>
        <div v-if="action === 'purchase'" class="field">
          <label>Цена закупки за ед., ₸</label><input v-model="actionForm.unit_cost" type="number" min="0" />
        </div>
        <div v-if="action === 'adjust'" class="field">
          <label>Фактический остаток</label><input v-model="actionForm.new_quantity" type="number" min="0" />
        </div>
        <div v-if="action === 'consume'" class="field">
          <label>Бронь (опционально)</label><input v-model="actionForm.booking_id" type="number" min="1" />
        </div>
        <div class="field span-2">
          <label>{{ action === 'purchase' ? 'Примечание' : 'Причина (обязательно)' }}</label>
          <input v-model="actionForm.reason" type="text" />
        </div>
      </div>
      <div class="row-actions">
        <button class="btn btn-primary" type="button" :disabled="saving" @click="submitAction">Записать</button>
        <button class="btn btn-ghost" type="button" @click="action = null">Отмена</button>
      </div>
    </div>

    <div v-if="historyProduct" class="card form-card">
      <h3>Журнал: {{ historyProduct.name }}</h3>
      <div v-if="movements.length === 0" class="muted">Движений нет</div>
      <div v-else class="table-wrap">
        <table class="table">
          <thead><tr><th>Дата</th><th>Тип</th><th>Δ</th><th>Продажа</th><th>Примечание</th></tr></thead>
          <tbody>
            <tr v-for="m in movements" :key="m.id">
              <td class="muted">{{ fmtTime(m.created_at) }}</td>
              <td>{{ KIND_LABELS[m.kind] ?? m.kind }}</td>
              <td>{{ m.delta > 0 ? '+' : '' }}{{ fmtQty(m.delta) }}</td>
              <td class="muted">{{ m.sale_id ? `#${m.sale_id}` : '—' }}</td>
              <td class="muted">{{ m.note || '—' }}</td>
            </tr>
          </tbody>
        </table>
      </div>
      <div class="row-actions">
        <button class="btn btn-ghost" type="button" @click="historyProduct = null">Закрыть</button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.toolbar { display: flex; gap: 16px; margin-bottom: 12px; }
.form-card { margin-top: 16px; }
.form-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; margin-bottom: 12px; }
.field.span-2 { grid-column: span 2; }
.row-low { background: rgba(231, 76, 60, 0.06); }
.badge-low { background: rgba(231, 76, 60, 0.2); margin-left: 6px; }
</style>
