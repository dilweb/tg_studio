<script setup>
import { computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { sectionsFor } from '../nav'
import { authStore } from '../store/auth'

// open/close — мобильный режим: InternalView прячет сайдбар за бургером,
// open=true выдвигает панель (класс .open), клик по разделу её закрывает.
defineProps({ open: { type: Boolean, default: false } })
const emit = defineEmits(['close'])

const route = useRoute()
const router = useRouter()

const me = computed(() => authStore.me)
const sections = computed(() => (me.value ? sectionsFor(me.value.role) : []))

const ROLE_LABELS = { owner: 'Владелец', admin: 'Администратор', master: 'Мастер' }
const roleLabel = computed(() => ROLE_LABELS[me.value?.role] ?? '')

function logout() {
  authStore.logout()
  router.replace('/login')
}

const icons = {
  grid: 'M3 3h7v7H3zM14 3h7v7h-7zM3 14h7v7H3zM14 14h7v7h-7z',
  calendar:
    'M8 2v4M16 2v4M3 10h18M5 4h14a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2Z',
  // wallet и clock есть в nav.js, но раньше отсутствовали здесь —
  // «Баланс», «Апсейлы» и «Смены» рисовались без иконки
  wallet:
    'M21 12V7H5a2 2 0 0 1 0-4h14v4M3 5v14a2 2 0 0 0 2 2h16v-5M18 12a2 2 0 0 0 0 4h4v-4Z',
  clock: 'M12 22a10 10 0 1 0 0-20 10 10 0 0 0 0 20ZM12 6v6l4 2',
  box:
    'M21 16V8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16ZM3.3 7.1 12 12l8.7-4.9M12 22V12',
  users:
    'M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2M23 21v-2a4 4 0 0 0-3-3.87M16 3.13a4 4 0 0 1 0 7.75M9 11a4 4 0 1 0 0-8 4 4 0 0 0 0 8Z',
  briefcase:
    'M20 7H4a2 2 0 0 0-2 2v10a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2V9a2 2 0 0 0-2-2ZM16 21V5a2 2 0 0 0-2-2h-4a2 2 0 0 0-2 2v16',
}
</script>

<template>
  <aside class="sidebar" :class="{ open }">
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
        @click="emit('close')"
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
  /* 100% от .layout (height: 100%): 100vh в Telegram WebView выше видимой области */
  height: 100%;
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

/* --- Мобильный режим: выдвижная шторка поверх контента --- */
@media (max-width: 767px) {
  .sidebar {
    position: fixed;
    top: 0;
    left: 0;
    bottom: 0;
    height: auto;
    width: 270px;
    max-width: 84vw;
    z-index: 30;
    transform: translateX(-100%);
    transition: transform 0.22s ease-out;
    box-shadow: 0 0 40px rgba(0, 0, 0, 0.4);
    padding-bottom: calc(16px + env(safe-area-inset-bottom, 0px));
    overflow-y: auto;
  }

  .sidebar.open {
    transform: translateX(0);
  }

  /* Тач-таргеты пунктов меню */
  .nav-item {
    padding: 12px 10px;
  }
}
</style>
