<script setup>
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'

import { hasInitData } from '../api/client'
import { login } from '../store/auth'

const router = useRouter()

const error = ref('')
const busy = ref(false)

// В Telegram — автологин по initData; вне Telegram входа нет (debug-форма
// удалена: бэкенд больше не принимает X-Debug-User-Id)
const telegramMode = ref(hasInitData())

onMounted(async () => {
  if (!telegramMode.value) return
  busy.value = true
  try {
    await login()
    router.replace('/')
  } catch (err) {
    error.value =
      err.status === 403
        ? (err.detail ?? 'Доступ только для админа или мастера')
        : `Не удалось войти через Telegram${err.message ? `: ${err.message}` : ''}`
  } finally {
    busy.value = false
  }
})
</script>

<template>
  <div class="login-wrap">
    <div class="login-card card">
      <div class="brand-row">
        <div class="logo">TS</div>
        <div class="brand-name">TG Studio</div>
      </div>
      <p class="subtitle">Внутренняя панель для админа и мастеров</p>

      <div v-if="error" class="error-box">{{ error }}</div>

      <div v-if="telegramMode" class="telegram-state">
        <template v-if="busy">Входим через Telegram…</template>
      </div>
      <p v-else class="telegram-state">Вход только через Telegram — откройте приложение в боте студии.</p>
    </div>
  </div>
</template>

<style scoped>
.login-wrap {
  min-height: 100%;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 24px;
}

.login-card {
  width: 100%;
  max-width: 380px;
}

.brand-row {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 6px;
}

.logo {
  width: 40px;
  height: 40px;
  border-radius: 12px;
  background: var(--accent);
  display: flex;
  align-items: center;
  justify-content: center;
  font-weight: 700;
  font-size: 15px;
  color: #fff;
}

.brand-name {
  font-size: 18px;
  font-weight: 700;
}

.subtitle {
  color: var(--muted);
  margin-bottom: 24px;
  font-size: 13px;
}

.telegram-state {
  color: var(--muted);
  text-align: center;
  padding: 12px 0;
}
</style>