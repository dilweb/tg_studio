<script setup>
import { onMounted, reactive, ref } from 'vue'

import { api } from '../api/client'

// «Администраторы» (05): владелец назначает/снимает администраторов студии по telegram_id.
const admins = ref([])
const loading = ref(true)
const error = ref('')
const notice = ref('')
const saving = ref(false)

const showForm = ref(false)
const form = reactive({ telegram_id: '', full_name: '' })

async function load() {
  loading.value = true
  error.value = ''
  try {
    admins.value = (await api.get('/api/admins')).admins
  } catch (err) {
    error.value = err.detail ?? err.message
  } finally {
    loading.value = false
  }
}

async function submit() {
  if (!form.telegram_id || !form.full_name.trim()) {
    error.value = 'Укажите Telegram ID и имя'
    return
  }
  saving.value = true
  error.value = ''
  try {
    await api.post('/api/admins', {
      telegram_id: Number(form.telegram_id),
      full_name: form.full_name.trim(),
    })
    notice.value = 'Администратор добавлен'
    showForm.value = false
    form.telegram_id = ''
    form.full_name = ''
    await load()
  } catch (err) {
    error.value = err.detail ?? err.message
  } finally {
    saving.value = false
  }
}

async function remove(a) {
  if (!window.confirm(`Снять администратора ${a.full_name}?`)) return
  try {
    await api.del(`/api/admins/${a.id}`)
    notice.value = 'Администратор снят'
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
      <h2>Администраторы</h2>
      <div class="row-actions">
        <button class="btn btn-primary" type="button" @click="showForm = true">+ Администратор</button>
      </div>
    </div>

    <div v-if="error" class="error-box">{{ error }}</div>
    <div v-if="notice" class="success-box">{{ notice }}</div>

    <div v-if="loading" class="muted">Загрузка…</div>

    <div v-else class="card">
      <div v-if="admins.length === 0" class="empty-state">
        <div class="empty-title">Администраторов пока нет</div>
      </div>
      <div v-else class="table-wrap">
        <table class="table">
          <thead>
            <tr><th>Имя</th><th>Telegram ID</th><th>Статус</th><th class="actions-col"></th></tr>
          </thead>
          <tbody>
            <tr v-for="a in admins" :key="a.id">
              <td>{{ a.full_name }}</td>
              <td class="muted">{{ a.telegram_id }}</td>
              <td>
                <span class="badge" :class="{ 'badge-ok': a.is_active }">
                  {{ a.is_active ? 'Активен' : 'Снят' }}
                </span>
              </td>
              <td class="actions-col">
                <button
                  v-if="a.is_active"
                  class="btn btn-sm btn-ghost"
                  type="button"
                  @click="remove(a)"
                >Снять</button>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>

    <div v-if="showForm" class="card form-card">
      <h3>Новый администратор</h3>
      <div class="form-grid">
        <div class="field">
          <label>Telegram ID</label>
          <input v-model="form.telegram_id" type="number" placeholder="например: 123456789" />
        </div>
        <div class="field">
          <label>Имя</label>
          <input v-model="form.full_name" type="text" placeholder="Имя администратора" />
        </div>
      </div>
      <div class="row-actions">
        <button class="btn btn-primary" type="button" :disabled="saving" @click="submit">Добавить</button>
        <button class="btn btn-ghost" type="button" @click="showForm = false">Отмена</button>
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
.badge-ok {
  background: rgba(46, 204, 113, 0.15);
}
</style>
