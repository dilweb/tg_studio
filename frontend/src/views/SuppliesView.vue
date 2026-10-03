<script setup>
import { computed, onMounted, reactive, ref } from 'vue'

import { api } from '../api/client'
import { authStore } from '../store/auth'

const CATEGORIES = ['Краска', 'Иглы и картриджи', 'Перчатки', 'Гигиена', 'Уход', 'Прочее']
const UNITS = ['шт', 'мл', 'г', 'упаковка']

const KIND_LABELS = {
  init: 'Начальный остаток',
  purchase: 'Закупка',
  use: 'Списание',
  adjust: 'Корректировка',
}

const isOwner = computed(() => authStore.me?.role === 'owner')

const supplies = ref([])
const loading = ref(true)
const error = ref('')
const notice = ref('')

const filterCategory = ref('')
const lowOnly = ref(false)
const showInactive = ref(false)

// --- форма создания/правки позиции (владелец) ---
const showForm = ref(false)
const editingId = ref(null)
const form = reactive({
  name: '',
  category: CATEGORIES[0],
  unit: 'шт',
  quantity: '0',
  min_quantity: '',
  note: '',
})

// --- диалог движения: use (обе роли) / restock, adjust (владелец) ---
const actionType = ref(null) // 'use' | 'restock' | 'adjust'
const actionSupply = ref(null)
const saving = ref(false)
const actionForm = reactive({
  amount: '',
  new_quantity: '',
  note: '',
  work_id: '',
  session_id: '',
})

// работы для привязки списания (лениво, при открытии «Списать»)
const works = ref([])
const worksLoading = ref(false)
const selectedWork = computed(() => works.value.find((w) => String(w.id) === String(actionForm.work_id)))

// --- журнал движений ---
const historySupply = ref(null)
const movements = ref([])
const movementsLoading = ref(false)

function fmtQty(v) {
  return String(v).replace(/(\.\d\d)0+$/, '$1').replace(/(\.\d)0$/, '$1')
}

async function load() {
  loading.value = true
  error.value = ''
  try {
    const params = new URLSearchParams()
    if (filterCategory.value) params.set('category', filterCategory.value)
    if (lowOnly.value) params.set('low_only', 'true')
    if (showInactive.value) params.set('include_inactive', 'true')
    supplies.value = (await api.get(`/api/supplies?${params}`)).supplies
  } catch (err) {
    error.value = err.detail ?? err.message
  } finally {
    loading.value = false
  }
}

// --- создать / править ---
function openCreate() {
  editingId.value = null
  Object.assign(form, { name: '', category: CATEGORIES[0], unit: 'шт', quantity: '0', min_quantity: '', note: '' })
  error.value = ''
  showForm.value = true
}

function openEdit(s) {
  editingId.value = s.id
  Object.assign(form, {
    name: s.name,
    category: s.category,
    unit: s.unit,
    quantity: '',
    min_quantity: s.min_quantity === null || s.min_quantity === undefined ? '' : String(s.min_quantity),
    note: s.note ?? '',
  })
  error.value = ''
  showForm.value = true
}

async function submitForm() {
  if (!form.name.trim()) {
    error.value = 'Введите название'
    return
  }
  saving.value = true
  error.value = ''
  try {
    const body = {
      name: form.name.trim(),
      category: form.category,
      unit: form.unit,
      note: form.note.trim() || null,
      min_quantity: form.min_quantity === '' ? null : Number(form.min_quantity),
    }
    if (editingId.value === null) {
      await api.post('/api/supplies', { ...body, quantity: Number(form.quantity) || 0 })
      notice.value = 'Позиция добавлена'
    } else {
      await api.patch(`/api/supplies/${editingId.value}`, body)
      notice.value = 'Позиция обновлена'
    }
    showForm.value = false
    await load()
  } catch (err) {
    error.value = err.detail ?? err.message
  } finally {
    saving.value = false
  }
}

// --- движения ---
function openAction(type, s) {
  actionType.value = type
  actionSupply.value = s
  Object.assign(actionForm, { amount: '', new_quantity: '', note: '', work_id: '', session_id: '' })
  error.value = ''
  if (type === 'use') {
    loadWorks()
  }
}

async function loadWorks() {
  worksLoading.value = true
  try {
    works.value = (await api.get('/api/tattoo/works')).works
  } catch {
    works.value = [] // привязка необязательна — списание работает и без неё
  } finally {
    worksLoading.value = false
  }
}

async function submitAction() {
  const s = actionSupply.value
  if (!s) return
  saving.value = true
  error.value = ''
  try {
    let body
    if (actionType.value === 'adjust') {
      if (actionForm.new_quantity === '' || Number(actionForm.new_quantity) < 0) {
        error.value = 'Введите новый остаток'
        return
      }
      body = { new_quantity: Number(actionForm.new_quantity), note: actionForm.note.trim() || null }
    } else {
      if (!actionForm.amount || Number(actionForm.amount) <= 0) {
        error.value = 'Введите количество'
        return
      }
      body = {
        amount: Number(actionForm.amount),
        note: actionForm.note.trim() || null,
        work_id: actionForm.work_id ? Number(actionForm.work_id) : null,
        session_id: actionForm.session_id ? Number(actionForm.session_id) : null,
      }
    }
    const updated = await api.post(`/api/supplies/${s.id}/${actionType.value}`, body)
    notice.value = {
      use: `Списано ${fmtQty(body.amount)} ${s.unit}`,
      restock: `Пополнено на ${fmtQty(body.amount)} ${s.unit}`,
      adjust: `Остаток выставлен: ${fmtQty(updated.quantity)} ${s.unit}`,
    }[actionType.value]
    actionType.value = null
    actionSupply.value = null
    await load()
  } catch (err) {
    error.value = err.detail ?? err.message
  } finally {
    saving.value = false
  }
}

async function archive(s) {
  if (!window.confirm(`Убрать «${s.name}» со склада? История движений сохранится.`)) return
  try {
    await api.del(`/api/supplies/${s.id}`)
    notice.value = 'Позиция убрана в архив'
    await load()
  } catch (err) {
    error.value = err.detail ?? err.message
  }
}

// --- журнал ---
async function openHistory(s) {
  historySupply.value = s
  movementsLoading.value = true
  try {
    movements.value = (await api.get(`/api/supplies/movements?supply_id=${s.id}`)).movements
  } catch (err) {
    error.value = err.detail ?? err.message
  } finally {
    movementsLoading.value = false
  }
}

function fmtDate(iso) {
  return new Date(iso).toLocaleString('ru-RU', {
    day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit',
  })
}

function fmtSession(iso) {
  return new Date(iso).toLocaleString('ru-RU', {
    day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit',
  })
}

function lowLabel(s) {
  return `Осталось ${fmtQty(s.quantity)} ${s.unit} (минимум ${fmtQty(s.min_quantity)})`
}

onMounted(load)
</script>

<template>
  <div class="page">
    <div class="section-head">
      <h2>Склад</h2>
      <button v-if="isOwner" class="btn btn-primary" type="button" @click="openCreate">
        + Добавить позицию
      </button>
    </div>

    <div v-if="error" class="error-box">{{ error }}</div>
    <div v-if="notice" class="success-box">{{ notice }}</div>

    <div class="toolbar">
      <select v-model="filterCategory" @change="load">
        <option value="">Все категории</option>
        <option v-for="c in CATEGORIES" :key="c" :value="c">{{ c }}</option>
      </select>
      <label class="toolbar-toggle">
        <input v-model="lowOnly" type="checkbox" @change="load" />
        Только кончающиеся
      </label>
      <label v-if="isOwner" class="toolbar-toggle">
        <input v-model="showInactive" type="checkbox" @change="load" />
        Показать архив
      </label>
      <div class="spacer"></div>
    </div>

    <div v-if="loading" class="muted">Загрузка…</div>
    <div v-else-if="supplies.length === 0" class="empty-state">
      <div class="empty-title">На складе пока ничего нет</div>
      <div v-if="isOwner" class="muted">Добавь краску, иглы, перчатки — мастера будут списывать из остатка.</div>
    </div>

    <div v-else class="table-wrap">
      <table class="table">
        <thead>
          <tr>
            <th>Позиция</th>
            <th>Категория</th>
            <th>Остаток</th>
            <th>Минимум</th>
            <th class="actions-col">Действия</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="s in supplies" :key="s.id" :class="{ 'row-low': s.low, 'row-inactive': !s.is_active }">
            <td>
              <div class="name">{{ s.name }}</div>
              <div v-if="!s.is_active" class="badge badge-muted">Архив</div>
              <div v-else-if="s.low" class="badge badge-red">Кончается</div>
              <div v-else-if="s.note" class="muted note-line">{{ s.note }}</div>
            </td>
            <td>{{ s.category }}</td>
            <td class="qty">{{ fmtQty(s.quantity) }} {{ s.unit }}</td>
            <td class="muted">{{ s.min_quantity === null ? '—' : `${fmtQty(s.min_quantity)} ${s.unit}` }}</td>
            <td class="actions-col">
              <div class="row-actions">
                <button
                  class="btn btn-sm"
                  type="button"
                  :disabled="!s.is_active"
                  @click="openAction('use', s)"
                >
                  − Списать
                </button>
                <template v-if="isOwner">
                  <button class="btn btn-sm btn-ghost" type="button" :disabled="!s.is_active" @click="openAction('restock', s)">
                    + Пополнить
                  </button>
                  <button class="btn btn-sm btn-ghost" type="button" :disabled="!s.is_active" @click="openAction('adjust', s)">
                    ✓ Ревизия
                  </button>
                  <button class="btn btn-sm btn-ghost" type="button" @click="openEdit(s)">Править</button>
                  <button class="btn btn-sm btn-ghost" type="button" @click="openHistory(s)">История</button>
                  <button v-if="s.is_active" class="btn btn-sm btn-ghost" type="button" @click="archive(s)">В архив</button>
                  <button v-else class="btn btn-sm btn-ghost" type="button" @click="openEdit(s)">Вернуть</button>
                </template>
              </div>
            </td>
          </tr>
        </tbody>
      </table>
    </div>

    <!-- форма позиции (владелец) -->
    <div v-if="showForm" class="card form-card">
      <h3>{{ editingId === null ? 'Новая позиция' : 'Правка позиции' }}</h3>
      <div class="form-grid">
        <div class="field span-2">
          <label>Название</label>
          <input v-model="form.name" type="text" placeholder="Чёрная краска Intenze 30ml" />
        </div>
        <div class="field">
          <label>Категория</label>
          <select v-model="form.category">
            <option v-for="c in CATEGORIES" :key="c" :value="c">{{ c }}</option>
          </select>
        </div>
        <div class="field">
          <label>Единица</label>
          <select v-model="form.unit">
            <option v-for="u in UNITS" :key="u" :value="u">{{ u }}</option>
          </select>
        </div>
        <div v-if="editingId === null" class="field">
          <label>Начальный остаток</label>
          <input v-model="form.quantity" type="number" min="0" step="0.01" />
        </div>
        <div class="field">
          <label>Минимум (порог докупки)</label>
          <input v-model="form.min_quantity" type="number" min="0" step="0.01" placeholder="не следим" />
        </div>
        <div class="field span-2">
          <label>Заметка</label>
          <input v-model="form.note" type="text" placeholder="где покупаем, чем заменяем…" />
        </div>
      </div>
      <div class="form-actions">
        <button class="btn btn-ghost" type="button" @click="showForm = false">Отмена</button>
        <button class="btn btn-primary" type="button" :disabled="saving" @click="submitForm">
          {{ editingId === null ? 'Добавить' : 'Сохранить' }}
        </button>
      </div>
    </div>

    <!-- диалог движения -->
    <div v-if="actionType && actionSupply" class="card form-card">
      <h3>
        {{
          { use: `Списать: ${actionSupply.name}`, restock: `Пополнить: ${actionSupply.name}`, adjust: `Ревизия: ${actionSupply.name}` }[actionType]
        }}
      </h3>
      <div class="form-grid">
        <div v-if="actionType === 'adjust'" class="field">
          <label>Новый остаток ({{ actionSupply.unit }})</label>
          <input v-model="actionForm.new_quantity" type="number" min="0" step="0.01" />
        </div>
        <div v-else class="field">
          <label>Количество ({{ actionSupply.unit }})</label>
          <input v-model="actionForm.amount" type="number" min="0" step="0.01" />
        </div>
        <div v-if="actionType === 'use'" class="field">
          <label>К работе (необязательно)</label>
          <select v-model="actionForm.work_id">
            <option value="">— не привязывать —</option>
            <option v-for="w in works" :key="w.id" :value="String(w.id)">
              #{{ w.id }} · {{ w.style }}
            </option>
          </select>
        </div>
        <div v-if="actionType === 'use' && selectedWork" class="field">
          <label>К сеансу</label>
          <select v-model="actionForm.session_id">
            <option value="">— вся работа —</option>
            <option v-for="ses in selectedWork.sessions" :key="ses.id" :value="String(ses.id)">
              {{ fmtSession(ses.session_date) }}
            </option>
          </select>
        </div>
        <div class="field span-2">
          <label>Комментарий</label>
          <input v-model="actionForm.note" type="text" placeholder="например: сеанс #8, реализм" />
        </div>
      </div>
      <div class="form-actions">
        <button class="btn btn-ghost" type="button" @click="actionType = null">Отмена</button>
        <button class="btn btn-primary" type="button" :disabled="saving" @click="submitAction">
          {{ { use: 'Списать', restock: 'Пополнить', adjust: 'Выставить' }[actionType] }}
        </button>
      </div>
    </div>

    <!-- журнал позиции -->
    <div v-if="historySupply" class="card form-card">
      <div class="section-head">
        <h3>История: {{ historySupply.name }}</h3>
        <button class="btn btn-sm btn-ghost" type="button" @click="historySupply = null">Закрыть</button>
      </div>
      <div v-if="movementsLoading" class="muted">Загрузка…</div>
      <div v-else-if="movements.length === 0" class="muted">Движений ещё не было</div>
      <div v-else class="table-wrap">
        <table class="table">
          <thead>
            <tr>
              <th>Когда</th>
              <th>Тип</th>
              <th>Сколько</th>
              <th>Кто</th>
              <th>Работа</th>
              <th>Комментарий</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="m in movements" :key="m.id">
              <td class="muted">{{ fmtDate(m.created_at) }}</td>
              <td>
                <span
                  class="badge"
                  :class="m.delta >= 0 ? 'badge-green' : 'badge-red'"
                >{{ KIND_LABELS[m.kind] ?? m.kind }}</span>
              </td>
              <td class="qty">{{ m.delta >= 0 ? '+' : '' }}{{ fmtQty(m.delta) }} {{ historySupply.unit }}</td>
              <td>{{ m.master_name ?? 'владелец' }}</td>
              <td class="muted">{{ m.work_id ? `#${m.work_id}` : '—' }}</td>
              <td class="muted">{{ m.note ?? '—' }}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>
  </div>
</template>

<style scoped>
.page {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.section-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 0;
}

.toolbar-toggle {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 13px;
  color: var(--muted);
  cursor: pointer;
}

.form-card {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.form-card h3 {
  margin: 0;
  font-size: 15px;
}

.span-2 {
  grid-column: span 2;
}

.form-actions {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
}

.row-low td {
  background: rgba(220, 60, 60, 0.06);
}

.row-inactive {
  opacity: 0.55;
}

.qty {
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
}

.name {
  font-weight: 600;
}

.note-line {
  font-size: 12px;
}

.actions-col {
  white-space: nowrap;
}

.row-actions {
  display: flex;
  gap: 6px;
  flex-wrap: wrap;
}
</style>
