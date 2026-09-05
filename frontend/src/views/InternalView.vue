<script setup>
import { computed } from 'vue'
import { useRoute } from 'vue-router'

import SideBar from '../components/SideBar.vue'
import { authStore } from '../store/auth'

const route = useRoute()

const sectionTitle = computed(() => route.meta.title ?? 'Панель')
const me = computed(() => authStore.me)

const roleLabel = computed(() => (me.value?.role === 'admin' ? 'Админ' : 'Мастер'))
</script>

<template>
  <div class="layout">
    <SideBar />
    <main class="main">
      <header class="topbar">
        <h1 class="section-title">{{ sectionTitle }}</h1>
        <div class="user-chip">
          <span class="user-role">{{ roleLabel }}</span>
          <span class="user-name">{{ me?.name }}</span>
        </div>
      </header>
      <div class="content">
        <router-view />
      </div>
    </main>
  </div>
</template>

<style scoped>
.layout {
  min-height: 100%;
  display: flex;
}

.main {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
}

.topbar {
  height: 56px;
  border-bottom: 1px solid var(--border);
  background: var(--surface);
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0 24px;
}

.section-title {
  font-size: 16px;
  font-weight: 600;
}

.user-chip {
  display: flex;
  align-items: center;
  gap: 10px;
}

.user-role {
  font-size: 11px;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.04em;
  color: var(--accent);
  background: rgba(108, 99, 255, 0.12);
  border: 1px solid rgba(108, 99, 255, 0.35);
  border-radius: 999px;
  padding: 3px 10px;
}

.user-name {
  color: var(--muted);
  font-size: 13px;
}

.content {
  flex: 1;
  padding: 24px;
}
</style>