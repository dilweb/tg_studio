<script setup>
import { onMounted } from 'vue'

import { refreshMe } from './store/auth'

// Фоновая ревалидация: localStorage-кэш мог протухнуть (роль сменили, пользователя удалили)
onMounted(() => {
  refreshMe()
})
</script>

<template>
  <router-view />
</template>