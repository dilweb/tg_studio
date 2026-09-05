<script setup>
import { computed } from 'vue'
import { useRoute } from 'vue-router'

// Заглушка для всех разделов; титул берётся из route.meta.title (router/index.js)
const route = useRoute()
const title = computed(() => route.meta.title ?? '')
</script>

<template>
  <div class="card">
    <div class="empty-state">
      <svg
        width="40"
        height="40"
        viewBox="0 0 24 24"
        fill="none"
        stroke="currentColor"
        stroke-width="1.5"
        stroke-linecap="round"
        stroke-linejoin="round"
      >
        <path d="M12 2 2 7l10 5 10-5-10-5Z" />
        <path d="m2 17 10 5 10-5" />
        <path d="m2 12 10 5 10-5" />
      </svg>
      <div class="empty-title">{{ title }}</div>
      <div>Раздел в разработке</div>
    </div>
  </div>
</template>
