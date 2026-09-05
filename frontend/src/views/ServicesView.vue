<script setup>
import { onMounted, reactive, ref } from 'vue'

import { api } from '../api/client'
import { SERVICE_TYPE } from '../labels'
import { formatMoney } from '../utils/format'

const items = ref([])
const loading = ref(true)
const error = ref('')
const saving = ref(false)
const showForm = ref(false)
// null → создание, число → правка существующей
const editingId = ref(null)

const form = reactive({
  name: '',
  description: '',
  service_type: 'appointment',
  price: '',
  prepayment_percent: 50,
  cancel_deadline_hours: 3,
})

async function load() {
  loading.value = true
  error.value = ''
  try {
    items.value = await api.get('/api/admin/services')
  } catch (err) {
    error.value = err.detail ?? err.message
  } finally {
    loading.value = false
  }
}

function openCreate() {
  editingId.value = null
  Object.assign(form, {
    name: '',
    description: '',
    service_type: 'appointment',
    price: '',
    prepayment_percent: 50,
    cancel_deadline_hours: 3,
  })
  error.value = ''
  showForm.value = true
}

function openEdit(s) {
  editingId.value = s.id
  Object.assign(form, {
    name: s.name,
    description: s.description ?? '',
    service_type: s.service_type,
    price: String(s.price),
    prepayment_percent: s.prepayment_percent,
    cancel_deadline_hours: s.cancel_deadline_hours,
  })
  error.value = ''
  showForm.value = true
}

async function submit() {
  if (!form.name.trim()) {
    error.value = 'Введите название услуги'
    return
  }
  const price = Number(form.price)
  if (!price || price <= 0) {
    error.value = 'Цена должна быть больше нуля'
    return
  }
  const prepayment = Number(form.prepayment_percent)
  if (Number.isNaN(prepayment) || prepayment < 0 || prepayment > 100) {
    error.value = 'Предоплата — число от 0 до 100'
    return
  }
  const cancelDeadline = Number(form.cancel_deadline_hours)
  if (Number.isNaN(cancelDeadline) || cancelDeadline < 0) {
    error.value = 'Дедлайн отмены — число часов, не меньше 0'
    return
  }

  saving.value = true
  error.value = ''
  try {
    const body = {
      name: form.name.trim(),
      description: form.description.trim(),
      price,
      prepayment_percent: Math.round(prepayment),
      cancel_deadline_hours: Math.round(cancelDeadline),
    }
    if (editingId.value === null) {
      await api.post('/api/admin/services', { ...body, service_type: form.service_type })
    } else {
      await api.patch(`/api/admin/services/${editingId.value}`, body)
    }
    showForm.value = false
    await load()
  } catch (err) {
    error.value = err.detail ?? err.message
  } finally {
    saving.value = false
  }
}

async function toggleActive(s) {
  error.value = ''
  try {
    await api.patch(`/api/admin/services/${s.id}`, { is_active: !s.is_active })
    await load()
  } catch (err) {
    error.value = err.detail ?? err.message
  }
}

onMounted(load)
</script>

<template>
  <div>
    <div v-if="error" class="error-box">{{ error }}</div>

    <div class="toolbar">
      <button class="btn btn-primary btn-sm" @click="openCreate">+ Добавить услугу</button>
    </div>

    <form v-if="showForm" class="card" style="margin-bottom: 16px" @submit.prevent="submit">
      <div class="form-grid">
        <div class="field">
          <label for="svc-name">Название</label>
          <input id="svc-name" v-model="form.name" autocomplete="off" />
        </div>
        <div class="field">
          <label for="svc-type">Тип</label>
          <select id="svc-type" v-model="form.service_type" :disabled="editingId !== null">
            <option value="appointment">По записи (почасовые слоты)</option>
            <option value="project">Проект (фиксированная цена)</option>
          </select>
        </div>
        <div class="field">
          <label for="svc-price">Цена, ₸</label>
          <input id="svc-price" v-model="form.price" inputmode="decimal" autocomplete="off" />
        </div>
        <div class="field">
          <label for="svc-prepay">Предоплата, %</label>
          <input id="svc-prepay" v-model="form.prepayment_percent" inputmode="numeric" autocomplete="off" />
        </div>
        <div class="field">
          <label for="svc-cancel">Отмена не позднее, ч</label>
          <input id="svc-cancel" v-model="form.cancel_deadline_hours" inputmode="numeric" autocomplete="off" />
        </div>
      </div>
      <div class="field">
        <label for="svc-desc">Описание</label>
        <textarea id="svc-desc" v-model="form.description" rows="2"></textarea>
      </div>
      <div style="display: flex; gap: 10px">
        <button class="btn btn-primary" type="submit" :disabled="saving">
          {{ saving ? 'Сохраняем…' : editingId === null ? 'Создать' : 'Сохранить' }}
        </button>
        <button class="btn btn-ghost" type="button" @click="showForm = false">Отмена</button>
      </div>
    </form>

    <div v-if="loading" class="muted">Загрузка…</div>

    <div v-else-if="!items.length" class="card">
      <div class="empty-state">
        <div class="empty-title">Услуг пока нет</div>
        <div>Добавьте первую услугу — она появится у клиентов при записи</div>
        <button class="btn btn-primary btn-sm" @click="openCreate">+ Добавить услугу</button>
      </div>
    </div>

    <div v-else class="table-wrap">
      <table class="table">
        <thead>
          <tr>
            <th>Название</th>
            <th>Тип</th>
            <th>Цена</th>
            <th>Предоплата</th>
            <th>Отмена</th>
            <th>Статус</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="s in items" :key="s.id">
            <td>
              <div>{{ s.name }}</div>
              <div v-if="s.description" class="muted" style="font-size: 12px">{{ s.description }}</div>
            </td>
            <td><span class="badge badge-muted">{{ SERVICE_TYPE[s.service_type] ?? s.service_type }}</span></td>
            <td style="white-space: nowrap">{{ formatMoney(s.price) }} ₸</td>
            <td>{{ s.prepayment_percent }}%</td>
            <td>{{ s.cancel_deadline_hours }} ч</td>
            <td>
              <span class="badge" :class="s.is_active ? 'badge-green' : 'badge-muted'">
                {{ s.is_active ? 'Активна' : 'Выключена' }}
              </span>
            </td>
            <td style="text-align: right; white-space: nowrap">
              <button class="btn btn-sm" @click="openEdit(s)">Изменить</button>
              <button class="btn btn-sm btn-ghost" @click="toggleActive(s)">
                {{ s.is_active ? 'Выключить' : 'Включить' }}
              </button>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>
