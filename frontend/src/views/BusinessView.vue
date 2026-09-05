<script setup>
import { computed, onMounted, reactive, ref } from 'vue'

import { api } from '../api/client'
import { authStore } from '../store/auth'

const info = ref(null)
const loading = ref(true)
const error = ref('')
const saving = ref(false)
// null → ещё не сохраняли; true/false — из ответа PATCH payment-settings
const payConfigured = ref(null)

const form = reactive({
  merchant_id: '',
  secret_key: '',
})

const tgId = computed(() => authStore.me?.telegram_id)

onMounted(async () => {
  try {
    info.value = await api.get(`/api/business/by-owner/${tgId.value}`)
  } catch (err) {
    error.value = err.detail ?? err.message
  } finally {
    loading.value = false
  }
})

async function savePayment() {
  const merchant = form.merchant_id.trim()
  const secret = form.secret_key.trim()
  if (!merchant && !secret) {
    error.value = 'Заполните Merchant ID или Secret Key'
    return
  }
  if (merchant && Number.isNaN(Number(merchant))) {
    error.value = 'Merchant ID — число из my.freedompay.kz'
    return
  }

  saving.value = true
  error.value = ''
  try {
    const body = {}
    if (merchant) body.freedom_pay_merchant_id = Number(merchant)
    if (secret) body.freedom_pay_secret_key = secret
    const res = await api.patch('/api/admin/business/payment-settings', body)
    payConfigured.value = Boolean(res.freedom_pay_configured)
    form.secret_key = '' // секрет не держим в форме после сохранения
  } catch (err) {
    error.value = err.detail ?? err.message
  } finally {
    saving.value = false
  }
}
</script>

<template>
  <div>
    <div v-if="error" class="error-box">{{ error }}</div>

    <div v-if="loading" class="muted">Загрузка…</div>

    <template v-else-if="info">
      <div class="card" style="margin-bottom: 16px">
        <div class="section-head">
          <h2>{{ info.name }}</h2>
          <span class="badge" :class="info.is_active ? 'badge-green' : 'badge-muted'">
            {{ info.is_active ? 'Активен' : 'Выключен' }}
          </span>
        </div>
        <div v-if="info.description" style="margin-bottom: 12px">{{ info.description }}</div>
        <div style="display: flex; gap: 24px; flex-wrap: wrap; font-size: 13px">
          <div>
            <div class="muted">Телефон</div>
            <div>{{ info.phone ?? '—' }}</div>
          </div>
          <div>
            <div class="muted">Telegram владельца</div>
            <div>{{ info.owner_telegram_id }}</div>
          </div>
        </div>
      </div>

      <div class="card">
        <div class="section-head">
          <h2>Приём платежей — Freedom Pay</h2>
          <span v-if="payConfigured !== null" class="badge" :class="payConfigured ? 'badge-green' : 'badge-yellow'">
            {{ payConfigured ? 'Настроено' : 'Не настроено' }}
          </span>
        </div>
        <p class="muted" style="font-size: 13px; margin-bottom: 16px">
          Ключи из личного кабинета my.freedompay.kz. Нужны, чтобы клиенты могли вносить предоплату
          через бота. Secret key сохраняется на сервере и больше не показывается.
        </p>

        <form @submit.prevent="savePayment">
          <div class="form-grid">
            <div class="field">
              <label for="fp-merchant">Merchant ID</label>
              <input id="fp-merchant" v-model="form.merchant_id" inputmode="numeric" autocomplete="off" />
            </div>
            <div class="field">
              <label for="fp-secret">Secret Key</label>
              <input
                id="fp-secret"
                v-model="form.secret_key"
                type="password"
                :placeholder="payConfigured ? 'уже сохранён — введите, чтобы заменить' : ''"
                autocomplete="new-password"
              />
            </div>
          </div>
          <button class="btn btn-primary" type="submit" :disabled="saving">
            {{ saving ? 'Сохраняем…' : 'Сохранить настройки' }}
          </button>
        </form>
      </div>
    </template>

    <div v-else class="card">
      <div class="empty-state">
        <div class="empty-title">Бизнес не найден</div>
        <div>Похоже, профиль бизнеса ещё не создан</div>
      </div>
    </div>
  </div>
</template>
