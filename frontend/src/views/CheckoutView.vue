<script setup>
import { computed, onMounted, reactive, ref } from 'vue'

import { api } from '../api/client'
import { authStore } from '../store/auth'

// «Касса» (06): корзина, покупатель (мастер или разовый контакт), оплата,
// чек и возвраты. У мастера — каталог + покупка со своего баланса.
const isMaster = computed(() => authStore.me?.role === 'master')

const products = ref([])
const masters = ref([])
const balance = ref(null)
const cart = reactive({}) // product_id -> qty

const loading = ref(true)
const saving = ref(false)
const error = ref('')
const notice = ref('')

const buyerType = ref('master') // master | walkin
const buyer = reactive({ master_id: '', buyer_name: '', buyer_phone: '', payment_method: 'balance', note: '' })
const lastSale = ref(null)

const sales = ref([])
const refundSale = ref(null)
const refund = reactive({ reason: '', items: [] })

const PAYMENT_LABELS = { balance: 'С баланса', cash: 'Наличные', card: 'Карта' }
const STATUS_LABELS = { 'оплачена': 'Оплачена', 'возвращена': 'Возвращена', 'отменена': 'Отменена' }

const cartLines = computed(() =>
  Object.entries(cart)
    .map(([id, qty]) => ({ product: products.value.find((p) => p.id === Number(id)), qty }))
    .filter((l) => l.product && l.qty > 0),
)
const total = computed(() => cartLines.value.reduce((s, l) => s + l.product.sale_price * l.qty, 0))

function fmtMoney(v) {
  return `${Number(v).toLocaleString('ru-RU', { maximumFractionDigits: 2 })} ₸`
}

function fmtQty(v) {
  return Number(v).toLocaleString('ru-RU', { maximumFractionDigits: 2 })
}

function fmtTime(iso) {
  return new Date(iso).toLocaleString('ru-RU', {
    day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit',
  })
}

function errText(err) {
  const d = err.detail
  return typeof d === 'object' ? (d.message ?? JSON.stringify(d)) : (d ?? err.message)
}

async function load() {
  loading.value = true
  error.value = ''
  try {
    const calls = [api.get('/api/products'), api.get('/api/sales')]
    if (isMaster.value) {
      calls.push(api.get('/api/me/balance'))
    } else {
      calls.push(api.get('/api/admin/masters'))
    }
    const [pr, sr, extra] = await Promise.all(calls)
    products.value = pr.products.filter((p) => p.sale_price !== null && p.is_active)
    sales.value = sr.sales
    if (isMaster.value) balance.value = extra
    else masters.value = extra
  } catch (err) {
    error.value = errText(err)
  } finally {
    loading.value = false
  }
}

function add(p) {
  if (cart[p.id] === undefined) cart[p.id] = 1
}

function setQty(id, value) {
  const n = Number(value)
  if (!n || n <= 0) delete cart[id]
  else cart[id] = n
}

function clearCart() {
  Object.keys(cart).forEach((k) => delete cart[k])
}

async function submitSale() {
  if (!cartLines.value.length) {
    error.value = 'Добавьте товары в корзину'
    return
  }
  const body = {
    items: cartLines.value.map((l) => ({ supply_id: l.product.id, qty: l.qty })),
    payment_method: isMaster.value ? 'balance' : buyer.payment_method,
    note: buyer.note || null,
  }
  if (!isMaster.value) {
    if (buyerType.value === 'master') {
      if (!buyer.master_id) {
        error.value = 'Выберите мастера'
        return
      }
      body.master_id = Number(buyer.master_id)
    } else {
      if (!buyer.buyer_name.trim()) {
        error.value = 'Укажите имя покупателя'
        return
      }
      if (body.payment_method === 'balance') {
        error.value = 'С баланса может платить только мастер'
        return
      }
      body.buyer_name = buyer.buyer_name.trim()
      body.buyer_phone = buyer.buyer_phone || null
    }
  }
  saving.value = true
  error.value = ''
  notice.value = ''
  try {
    lastSale.value = await api.post('/api/sales', body)
    notice.value = `Продажа #${lastSale.value.id} оформлена`
    clearCart()
    buyer.note = ''
    await load()
  } catch (err) {
    error.value = errText(err)
  } finally {
    saving.value = false
  }
}

function openRefund(s) {
  refundSale.value = s
  refund.reason = ''
  refund.items = s.items.map((i) => ({ supply_id: i.supply_id, name: i.supply_name, max: i.qty, qty: i.qty }))
  error.value = ''
}

async function submitRefund(full) {
  saving.value = true
  error.value = ''
  try {
    const body = { reason: refund.reason }
    if (!full) {
      body.items = refund.items.filter((i) => i.qty > 0).map((i) => ({ supply_id: i.supply_id, qty: i.qty }))
      if (!body.items.length) {
        error.value = 'Укажите количество к возврату'
        saving.value = false
        return
      }
    }
    const s = await api.post(`/api/sales/${refundSale.value.id}/refund`, body)
    notice.value = `Возврат по продаже #${s.id} оформлен`
    refundSale.value = null
    await load()
  } catch (err) {
    error.value = errText(err)
  } finally {
    saving.value = false
  }
}

onMounted(load)
</script>

<template>
  <div class="page">
    <div class="section-head">
      <h2>{{ isMaster ? 'Каталог и покупки' : 'Касса' }}</h2>
    </div>

    <div v-if="error" class="error-box">{{ error }}</div>
    <div v-if="notice" class="success-box">{{ notice }}</div>

    <div v-if="isMaster && balance" class="card balance-card">
      <div class="balance-label">Мой баланс</div>
      <div class="balance-value" :class="{ negative: balance.balance < 0 }">{{ fmtMoney(balance.balance) }}</div>
    </div>

    <div v-if="loading" class="muted">Загрузка…</div>

    <template v-else>
      <div class="card">
        <h3>Каталог</h3>
        <div v-if="products.length === 0" class="empty-state"><div class="empty-title">Нет товаров на продажу</div></div>
        <div v-else class="product-grid">
          <div v-for="p in products" :key="p.id" class="product-card">
            <div>
              <strong>{{ p.name }}</strong>
              <div class="muted">{{ fmtMoney(p.sale_price) }} / {{ p.unit }}</div>
              <div class="muted small">Остаток: {{ fmtQty(p.quantity) }}</div>
            </div>
            <button class="btn btn-sm btn-primary" type="button" :disabled="p.quantity <= 0" @click="add(p)">
              {{ cart[p.id] ? `В корзине: ${cart[p.id]}` : 'В корзину' }}
            </button>
          </div>
        </div>
      </div>

      <div class="card">
        <h3>Корзина <span v-if="cartLines.length" class="muted">— {{ fmtMoney(total) }}</span></h3>
        <div v-if="!cartLines.length" class="muted">Корзина пуста</div>
        <div v-else class="cart-lines">
          <div v-for="l in cartLines" :key="l.product.id" class="cart-line">
            <span>{{ l.product.name }}</span>
            <input type="number" min="0" step="0.01" :value="l.qty" @input="setQty(l.product.id, $event.target.value)" />
            <span class="muted">{{ fmtMoney(l.product.sale_price * l.qty) }}</span>
          </div>
        </div>

        <div v-if="!isMaster" class="buyer-form">
          <div class="toolbar">
            <label><input v-model="buyerType" type="radio" value="master" /> мастер</label>
            <label><input v-model="buyerType" type="radio" value="walkin" /> разовый покупатель</label>
          </div>
          <div class="form-grid">
            <div v-if="buyerType === 'master'" class="field">
              <label>Мастер</label>
              <select v-model="buyer.master_id">
                <option value="">— выберите —</option>
                <option v-for="m in masters" :key="m.id" :value="m.id">{{ m.full_name }}</option>
              </select>
            </div>
            <template v-else>
              <div class="field"><label>Имя</label><input v-model="buyer.buyer_name" type="text" /></div>
              <div class="field"><label>Телефон</label><input v-model="buyer.buyer_phone" type="text" /></div>
            </template>
            <div class="field">
              <label>Оплата</label>
              <select v-model="buyer.payment_method">
                <option v-if="buyerType === 'master'" value="balance">С баланса мастера</option>
                <option value="cash">Наличные</option>
                <option value="card">Карта</option>
              </select>
            </div>
            <div class="field"><label>Примечание</label><input v-model="buyer.note" type="text" /></div>
          </div>
        </div>

        <div class="row-actions">
          <button class="btn btn-primary" type="button" :disabled="saving || !cartLines.length" @click="submitSale">
            Оплатить {{ fmtMoney(total) }}
          </button>
          <button v-if="cartLines.length" class="btn btn-ghost" type="button" @click="clearCart">Очистить</button>
        </div>
      </div>

      <div v-if="lastSale" class="card receipt">
        <h3>Чек #{{ lastSale.id }}</h3>
        <div class="muted">{{ fmtTime(lastSale.created_at) }} · {{ PAYMENT_LABELS[lastSale.payment_method] }}</div>
        <div v-for="i in lastSale.items" :key="i.id" class="cart-line">
          <span>{{ i.supply_name }} × {{ fmtQty(i.qty) }}</span><span>{{ fmtMoney(i.amount) }}</span>
        </div>
        <strong>Итого: {{ fmtMoney(lastSale.total_amount) }}</strong>
      </div>

      <div class="card">
        <h3>{{ isMaster ? 'Мои покупки' : 'Продажи' }}</h3>
        <div v-if="!sales.length" class="muted">Продаж ещё нет</div>
        <div v-else class="table-wrap">
          <table class="table">
            <thead>
              <tr><th>#</th><th>Дата</th><th>Покупатель</th><th>Сумма</th><th>Оплата</th><th>Статус</th><th v-if="!isMaster"></th></tr>
            </thead>
            <tbody>
              <tr v-for="s in sales" :key="s.id">
                <td>{{ s.id }}</td>
                <td class="muted">{{ fmtTime(s.created_at) }}</td>
                <td>{{ s.master_name ?? s.buyer_name ?? '—' }}</td>
                <td>{{ fmtMoney(s.total_amount) }}</td>
                <td class="muted">{{ PAYMENT_LABELS[s.payment_method] }}</td>
                <td>{{ STATUS_LABELS[s.status] ?? s.status }}</td>
                <td v-if="!isMaster">
                  <button v-if="s.status !== 'возвращена'" class="btn btn-sm btn-ghost" type="button" @click="openRefund(s)">Возврат</button>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>

      <div v-if="refundSale" class="card form-card">
        <h3>Возврат по продаже #{{ refundSale.id }}</h3>
        <div v-for="(i, idx) in refund.items" :key="i.supply_id" class="cart-line">
          <span>{{ i.name }} (продано {{ fmtQty(i.max) }})</span>
          <input v-model="i.qty" type="number" min="0" :max="i.max" step="0.01" />
        </div>
        <div class="field"><label>Причина</label><input v-model="refund.reason" type="text" /></div>
        <div class="row-actions">
          <button class="btn btn-primary" type="button" :disabled="saving || !refund.reason.trim()" @click="submitRefund(true)">Полный возврат</button>
          <button class="btn" type="button" :disabled="saving || !refund.reason.trim()" @click="submitRefund(false)">Частичный</button>
          <button class="btn btn-ghost" type="button" @click="refundSale = null">Отмена</button>
        </div>
      </div>
    </template>
  </div>
</template>

<style scoped>
.product-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(220px, 1fr)); gap: 12px; }
.product-card { display: flex; flex-direction: column; gap: 10px; justify-content: space-between; padding: 12px; border: 1px solid var(--border, #333); border-radius: 8px; }
.cart-lines { margin-bottom: 12px; }
.cart-line { display: flex; align-items: center; gap: 12px; justify-content: space-between; padding: 6px 0; }
.cart-line input { width: 90px; }
.buyer-form { margin: 16px 0; }
.toolbar { display: flex; gap: 16px; margin-bottom: 12px; }
.form-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }
.receipt { display: flex; flex-direction: column; gap: 8px; }
.small { font-size: 0.85em; }
</style>
