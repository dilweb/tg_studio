<script setup>
import { computed, onMounted, ref } from 'vue'

import { api } from '../api/client'
import PlaceholderSection from '../components/PlaceholderSection.vue'
import { authStore } from '../store/auth'

const WEEKDAYS = ['Пн', 'Вт', 'Ср', 'Чт', 'Пт', 'Сб', 'Вс']

const masters = ref([])
const masterId = ref(null)
// По строке на каждый день недели: включён + время + длительность слота
const days = ref([])
const loading = ref(true)
const error = ref('')
const saving = ref(false)
const savedFlash = ref(false)

const isAdmin = computed(() => authStore.me?.role === 'admin')

function toRows(schedule) {
  const byWeekday = new Map(schedule.map((e) => [e.weekday, e]))
  return WEEKDAYS.map((_, wd) => {
    const e = byWeekday.get(wd)
    return {
      weekday: wd,
      enabled: Boolean(e),
      start_time: e?.start_time ?? '10:00',
      end_time: e?.end_time ?? '18:00',
      slot_duration_minutes: e?.slot_duration_minutes ?? 60,
    }
  })
}

async function load() {
  loading.value = true
  error.value = ''
  try {
    masters.value = await api.get('/api/admin/masters')
    if (masters.value.length) {
      await selectMaster(masters.value[0].id)
    }
  } catch (err) {
    error.value = err.detail ?? err.message
  } finally {
    loading.value = false
  }
}

async function selectMaster(id) {
  masterId.value = Number(id)
  error.value = ''
  savedFlash.value = false
  try {
    const schedule = await api.get(`/api/admin/masters/${masterId.value}/schedule`)
    days.value = toRows(schedule)
  } catch (err) {
    error.value = err.detail ?? err.message
  }
}

async function save() {
  if (!masterId.value) return
  saving.value = true
  error.value = ''
  savedFlash.value = false
  try {
    const payload = days.value
      .filter((d) => d.enabled)
      .map((d) => ({
        weekday: d.weekday,
        start_time: d.start_time,
        end_time: d.end_time,
        slot_duration_minutes: Number(d.slot_duration_minutes) || 60,
      }))
    const res = await api.put(`/api/admin/masters/${masterId.value}/schedule`, payload)
    days.value = toRows(res)
    savedFlash.value = true
    setTimeout(() => {
      savedFlash.value = false
    }, 2000)
  } catch (err) {
    error.value = err.detail ?? err.message
  } finally {
    saving.value = false
  }
}

onMounted(() => {
  if (isAdmin.value) load()
  else loading.value = false
})
</script>

<template>
  <!-- У мастера пока нет своих API — только владелец бизнеса правит расписание -->
  <PlaceholderSection v-if="!isAdmin" />

  <div v-else>
    <div v-if="error" class="error-box">{{ error }}</div>

    <div v-if="loading" class="muted">Загрузка…</div>

    <div v-else-if="!masters.length" class="card">
      <div class="empty-state">
        <div class="empty-title">Нет мастеров</div>
        <div>Сначала добавьте мастеров — потом задайте им рабочий график</div>
        <router-link class="btn btn-primary btn-sm" to="/app/masters">Перейти к мастерам</router-link>
      </div>
    </div>

    <template v-else>
      <div class="toolbar">
        <select :value="masterId" @change="selectMaster($event.target.value)">
          <option v-for="m in masters" :key="m.id" :value="m.id">
            {{ m.full_name }}{{ m.is_active ? '' : ' (выключен)' }}
          </option>
        </select>
        <span class="muted" style="font-size: 13px">Отметьте рабочие дни и время</span>
      </div>

      <form class="card" @submit.prevent="save">
        <div style="display: flex; flex-direction: column; gap: 12px">
          <div
            v-for="d in days"
            :key="d.weekday"
            style="display: flex; align-items: center; gap: 14px; flex-wrap: wrap"
          >
            <label class="checkbox-row" style="width: 64px">
              <input v-model="d.enabled" type="checkbox" />
              <span style="font-weight: 600">{{ WEEKDAYS[d.weekday] }}</span>
            </label>
            <input
              v-model="d.start_time"
              type="time"
              :disabled="!d.enabled"
              style="background: var(--surface2); border: 1px solid var(--border); border-radius: 10px; padding: 8px 12px; color: var(--text)"
              :style="{ opacity: d.enabled ? 1 : 0.4 }"
            />
            <span class="muted">—</span>
            <input
              v-model="d.end_time"
              type="time"
              :disabled="!d.enabled"
              style="background: var(--surface2); border: 1px solid var(--border); border-radius: 10px; padding: 8px 12px; color: var(--text)"
              :style="{ opacity: d.enabled ? 1 : 0.4 }"
            />
            <label class="checkbox-row" :style="{ opacity: d.enabled ? 1 : 0.4 }">
              <span class="muted">слот,</span>
              <input
                v-model="d.slot_duration_minutes"
                type="number"
                min="15"
                step="15"
                :disabled="!d.enabled"
                style="width: 70px; background: var(--surface2); border: 1px solid var(--border); border-radius: 10px; padding: 8px 12px; color: var(--text)"
              />
              <span class="muted">мин</span>
            </label>
          </div>
        </div>

        <div style="display: flex; gap: 10px; margin-top: 20px; align-items: center">
          <button class="btn btn-primary" type="submit" :disabled="saving">
            {{ saving ? 'Сохраняем…' : 'Сохранить расписание' }}
          </button>
          <span v-if="savedFlash" class="badge badge-green">Сохранено</span>
        </div>
      </form>
    </template>
  </div>
</template>
