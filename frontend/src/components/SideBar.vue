<script setup>
import { computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { sectionsFor } from '../nav'
import { authStore } from '../store/auth'

const route = useRoute()
const router = useRouter()

const me = computed(() => authStore.me)
const sections = computed(() => (me.value ? sectionsFor(me.value.role) : []))

const roleLabel = computed(() => (me.value?.role === 'owner' ? 'Владелец' : 'Мастер'))

function logout() {
  authStore.logout()
  router.replace('/login')
}

const icons = {
  grid: 'M3 3h7v7H3zM14 3h7v7h-7zM3 14h7v7H3zM14 14h7v7h-7z',
  calendar:
    'M8 2v4M16 2v4M3 10h18M5 4h14a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2Z',
  box:
    'M21 16V8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16ZM3.3 7.1 12 12l8.7-4.9M12 22V12',
  users:
    'M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2M23 21v-2a4 4 0 0 0-3-3.87M16 3.13a4 4 0 0 1 0 7.75M9 11a4 4 0 1 0 0-8 4 4 0 0 0 0 8Z',
  chat: 'M21 11.5a8.38 8.38 0 0 1-.9 3.8 8.5 8.5 0 0 1-7.6 4.7 8.38 8.38 0 0 1-3.8-.9L3 21l1.9-5.7a8.38 8.38 0 0 1-.9-3.8 8.5 8.5 0 0 1 4.7-7.6 8.38 8.38 0 0 1 3.8-.9h.5a8.48 8.48 0 0 1 8 8v.5z',
  briefcase:
    'M20 7H4a2 2 0 0 0-2 2v10a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2V9a2 2 0 0 0-2-2ZM16 21V5a2 2 0 0 0-2-2h-4a2 2 0 0 0-2 2v16',
  ai: 'M12 3l-1.9 5.8a2 2 0 0 1-1.3 1.3L3 12l5.8 1.9a2 2 0 0 1 1.3 1.3L12 21l1.9-5.8a2 2 0 0 1 1.3-1.3L21 12l-5.8-1.9a2 2 0 0 1-1.3-1.3L12 3ZM19 15v4M17 17h4',
}
</script>

<template>
  <aside class="sidebar">
    <div class="brand">
      <div class="logo">TS</div>
      <span class="brand-name">TG Studio</span>
    </div>

    <div class="business-pill">
      <!-- /auth/me не отдаёт business_name — показываем имя пользователя -->
      <span class="business-name">{{ me?.first_name ?? 'TG Studio' }}</span>
      <span class="role-tag">{{ roleLabel }}</span>
    </div>

    <nav class="nav">
      <router-link
        v-for="section in sections"
        :key="section.name"
        :to="section.path"
        class="nav-item"
        :class="{ active: route.meta.name === section.name }"
      >
        <svg
          class="nav-icon"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          stroke-width="1.8"
          stroke-linecap="round"
          stroke-linejoin="round"
        >
          <path :d="icons[section.icon]" />
        </svg>
        <span>{{ section.title }}</span>
      </router-link>
    </nav>

    <button class="btn btn-ghost logout" type="button" @click="logout">Выйти</button>
  </aside>
</template>

<style scoped>
.sidebar {
  width: var(--sidebar-w);
  flex-shrink: 0;
  border-right: 1px solid var(--border);
  background: var(--surface);
  display: flex;
  flex-direction: column;
  padding: 16px 12px;
  position: sticky;
  top: 0;
  height: 100vh;
}

.brand {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 4px 8px 16px;
}

.logo {
  width: 32px;
  height: 32px;
  border-radius: 10px;
  background: var(--accent);
  display: flex;
  align-items: center;
  justify-content: center;
  font-weight: 700;
  font-size: 12px;
  color: #fff;
}

.brand-name {
  font-weight: 700;
  font-size: 15px;
}

.business-pill {
  background: var(--surface2);
  border: 1px solid var(--border);
  border-radius: 10px;
  padding: 10px 12px;
  margin-bottom: 16px;
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.business-name {
  font-size: 13px;
  font-weight: 600;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.role-tag {
  font-size: 11px;
  color: var(--muted);
}

.nav {
  display: flex;
  flex-direction: column;
  gap: 2px;
  flex: 1;
}

.nav-item {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 9px 10px;
  border-radius: 8px;
  color: var(--muted);
  font-size: 13.5px;
  font-weight: 500;
  transition: background 0.15s, color 0.15s;
}

.nav-item:hover {
  background: var(--surface2);
  color: var(--text);
}

.nav-item.active {
  background: rgba(108, 99, 255, 0.14);
  color: var(--accent);
}

.nav-icon {
  width: 17px;
  height: 17px;
  flex-shrink: 0;
}

.logout {
  margin-top: auto;
  justify-content: flex-start;
}
</style>