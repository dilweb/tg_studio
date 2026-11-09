<script setup>
import { computed, onMounted, reactive, ref } from 'vue'

import { api } from '../api/client'
import { authStore } from '../store/auth'

// Раздел «Смены» (05): админ видит свои смены и ведёт факт; владелец — все + утверждает часы.
const isOwner = computed(() => authStore.me?.role === 'owner')

const shifts = ref([])
const admins = ref([])
const loading = ref(true)
const error = ref('')
const notice = ref('')
const saving = ref(false)

const showForm = ref(false)
const form = reactive({ admin_id: '', planned_start: '', planned_end: '', note: '' })

const showApprove = ref(false)
const approveForm = reactive({ shift_id: null, approved_hours: '' })

function fmt(iso) {
  return new Date(iso).toLocaleString('ru-RU', {
    day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit',
  })
}

function todayStr() {
  const d = new Date()
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
}

async function load() {
  loading.value = true
  error.value = ''
  try {
    const from = new Date(Date.now() - 7 * 86400 * 1000)
    const to = new Date(Date.now() + 14 * 86400 * 1000)
    const params = `from=${from.toISOString()}&to=${to.toISOString()}`
    shifts.value = (await api.get(`/api/shifts?${params}`)).shifts
    if (isOwner.value) {
      admins.value = (await api.get('/api/admins')).admins
    }
  } catch (err) {
    error.value = err.detail ?? err.message
  } finally {
    loading.value = false
  }
}

function openCreate() {
  const base = todayStr()
  form.admin_id = isOwner.value ? (admins.value[0]?.id ?? '') : ''
  form.planned_start = `${base}T10:00`
  form.planned_end = `${base}T18:00`
  form.note = ''
  error.value = ''
  showForm.value = true
}

async function submitShift() {
  if (!form.planned_start || !form.planned_end) {
    error.value = 'Укажите интервал смены'
    return
  }
  saving.value = true
  error.value = ''
  try {
    const body = {
      admin_id: Number(form.admin_id),
      planned_start: form.planned_start,
      planned_end: form.planned_end,
      note: form.note || null,
    }
    await api.post('/api/shifts', body)
    notice.value = 'Смена создана'
    showForm.value = false
    await load()
  } catch (err) {
    error.value = err.detail ?? err.message
  } finally {
    saving.value = false
  }
}

async function act(s, action) {
  saving.value = true
  error.value = ''
  try {
    await api.post(`/api/shifts/${s.id}/${action}`)
    notice.value = action === 'start' ? 'Смена начата' : 'Смена завершена'
    await load()
  } catch (err) {
    error.value = err.detail ?? err.message
  } finally {
    saving.value = false
  }
}

function openApprove(s) {
  approveForm.shift_id = s.id
  approveForm.approved_hours = ''
  showApprove.value = true
}

async function submitApprove() {
  if (!approveForm.approved_hours) {
    error.value = 'Укажите часы'
    return
  }
  saving.value = true
  error.value = ''
  try {
    await api.patch(`/api/shifts/${approveForm.shift_id}/approve`, {
      approved_hours: Number(approveForm.approved_hours),
    })
    notice.value = 'Часы утверждены'
    showApprove.value = false
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
      <h2>Смены</h2>
      <div class="row-actions">
        <button class="btn btn-primary" type="button" @click="openCreate">+ Смена</button>
      </div>
    </div>

    <div v-if="error" class="error-box">{{ error }}</div>
    <div v-if="notice" class="success-box">{{ notice }}</div>

    <div v-if="loading" class="muted">Загрузка…</div>

    <div v-else class="card">
      <div v-if="shifts.length === 0" class="empty-state">
        <div class="empty-title">Смен нет</div>
      </div>
      <div v-else class="table-wrap">
        <table class="table">
          <thead>
            <tr>
              <th>Админ</th><th>План</th><th>Факт</th><th>Утверждено, ч</th><th>Примечание</th><th class="actions-col"></th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="s in shifts" :key="s.id">
              <td>{{ s.admin_name ?? `#${s.admin_id}` }}</td>
              <td class="muted">{{ fmt(s.planned_start) }} — {{ fmt(s.planned_end).slice(-5) }}</td>
              <td class="muted">
                <template v-if="s.started_at || s.ended_at">
                  {{ s.started_at ? fmt(s.started_at).slice(-5) : '—' }} – {{ s.ended_at ? fmt(s.ended_at).slice(-5) : '…' }}
                </template>
                <template v-else>—</template>
              </td>
              <td>
                <span v-if="s.approved_hours !== null" class="badge">{{ s.approved_hours }} ч</span>
                <span v-else class="muted">—</span>
              </td>
              <td class="muted">{{ s.note || '—' }}</td>
              <td class="actions-col">
                <button
                  v-if="!s.started_at"
                  class="btn btn-sm"
                  type="button"
                  :disabled="saving"
                  @click="act(s, 'start')"
                >Начать</button>
                <button
                  v-if="s.started_at && !s.ended_at"
                  class="btn btn-sm"
                  type="button"
                  :disabled="saving"
                  @click="act(s, 'end')"
                >Завершить</button>
                <button
                  v-if="isOwner && s.ended_at && s.approved_hours === null"
                  class="btn btn-sm btn-primary"
                  type="button"
                  :disabled="saving"
                  @click="openApprove(s)"
                >Утвердить часы</button>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>

    <div v-if="showForm" class="card form-card">
      <h3>Новая смена</h3>
      <div class="form-grid">
        <div v-if="isOwner" class="field">
          <label>Администратор</label>
          <select v-model="form.admin_id">
            <option v-for="a in admins" :key="a.id" :value="a.id">{{ a.full_name }}</option>
          </select>
        </div>
        <div class="field">
          <label>Начало</label>
          <input v-model="form.planned_start" type="datetime-local" />
        </div>
        <div class="field">
          <label>Конец</label>
          <input v-model="form.planned_end" type="datetime-local" />
        </div>
        <div class="field span-2">
          <label>Примечание</label>
          <input v-model="form.note" type="text" />
        </div>
      </div>
      <div class="row-actions">
        <button class="btn btn-primary" type="button" :disabled="saving" @click="submitShift">Создать</button>
        <button class="btn btn-ghost" type="button" @click="showForm = false">Отмена</button>
      </div>
    </div>

    <div v-if="showApprove" class="card form-card">
      <h3>Утверждение часов</h3>
      <div class="form-grid">
        <div class="field">
          <label>Оплачиваемые часы</label>
          <input v-model="approveForm.approved_hours" type="number" min="0" step="0.5" />
        </div>
      </div>
      <div class="row-actions">
        <button class="btn btn-primary" type="button" :disabled="saving" @click="submitApprove">Утвердить</button>
        <button class="btn btn-ghost" type="button" @click="showApprove = false">Отмена</button>
      </div>
    </div>
  </div>
</template>

<style scoped>
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
