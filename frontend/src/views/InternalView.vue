<script setup>
import { computed, ref, watch } from 'vue'
import { useRoute } from 'vue-router'

import SideBar from '../components/SideBar.vue'
import { authStore } from '../store/auth'
import { hideBackButton, showBackButton } from '../telegram'

const route = useRoute()

const sectionTitle = computed(() => route.meta.title ?? 'Панель')
const me = computed(() => authStore.me)

const ROLE_LABELS = { owner: 'Владелец', admin: 'Администратор', master: 'Мастер' }
const roleLabel = computed(() => ROLE_LABELS[me.value?.role] ?? '')
// /api/auth/me отдаёт first_name/last_name, поля name в ответе нет
const userLabel = computed(() =>
  [me.value?.first_name, me.value?.last_name].filter(Boolean).join(' '),
)

// Мобильная шторка меню: на <768px сайдбар превращается в выдвижной оверлей.
// Открытие поднимает Telegram BackButton — «назад» на Android закрывает меню,
// а не мини-апп целиком.
const drawerOpen = ref(false)

// Один обработчик на всё время жизни компонента: Telegram накапливает
// onClick-слушатели, поэтому один и тот же передаём и в show, и в hide
const closeDrawer = () => {
  drawerOpen.value = false
}

watch(
  () => route.fullPath,
  () => {
    drawerOpen.value = false
  },
)

watch(drawerOpen, (open) => {
  if (open) {
    showBackButton(closeDrawer)
  } else {
    hideBackButton(closeDrawer)
  }
})
</script>

<template>
  <div class="layout">
    <SideBar :open="drawerOpen" @close="drawerOpen = false" />
    <main class="main">
      <header class="topbar">
        <div class="topbar-left">
          <button
            class="burger"
            type="button"
            aria-label="Открыть меню"
            @click="drawerOpen = true"
          >
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round">
              <path d="M4 6h16M4 12h16M4 18h16" />
            </svg>
          </button>
          <h1 class="section-title">{{ sectionTitle }}</h1>
        </div>
        <div class="user-chip">
          <span class="user-role">{{ roleLabel }}</span>
          <span class="user-name">{{ userLabel }}</span>
        </div>
      </header>
      <div class="content">
        <router-view />
      </div>
    </main>
    <div v-if="drawerOpen" class="drawer-backdrop" @click="drawerOpen = false"></div>
  </div>
</template>

<style scoped>
.layout {
  /* Высота зафиксирована на вьюпорт: скроллится .content, а не вся страница.
     Иначе вью «Чатов» не получает definite-высоту и колонки скроллятся вместе. */
  height: 100%;
  display: flex;
}

.main {
  flex: 1;
  min-width: 0;
  min-height: 0;
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

.topbar-left {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
}

.burger {
  display: none;
  align-items: center;
  justify-content: center;
  width: 40px;
  height: 40px;
  margin-left: -8px;
  border: 0;
  border-radius: 10px;
  background: transparent;
  color: var(--text);
  cursor: pointer;
}

.burger svg {
  width: 22px;
  height: 22px;
}

.section-title {
  font-size: 16px;
  font-weight: 600;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.user-chip {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-shrink: 0;
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
  min-height: 0;
  overflow-y: auto;
}

.drawer-backdrop {
  position: fixed;
  inset: 0;
  background: rgba(0, 0, 0, 0.5);
  z-index: 20;
}

@media (max-width: 767px) {
  .burger {
    display: inline-flex;
  }

  .topbar {
    padding: 0 14px;
    padding-top: env(safe-area-inset-top, 0px);
    height: calc(56px + env(safe-area-inset-top, 0px));
  }

  /* Имя не влезает в 390px рядом с бургером и заголовком */
  .user-name {
    display: none;
  }

  .content {
    padding: 14px;
    padding-bottom: calc(14px + env(safe-area-inset-bottom, 0px));
  }
}
</style>
