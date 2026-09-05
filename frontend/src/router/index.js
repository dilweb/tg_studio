import { createRouter, createWebHistory } from 'vue-router'

import PlaceholderSection from '../components/PlaceholderSection.vue'
import { firstSectionPath } from '../nav'
import { authStore } from '../store/auth'
import BookingsView from '../views/BookingsView.vue'
import BusinessView from '../views/BusinessView.vue'
import LoginView from '../views/LoginView.vue'
import MastersView from '../views/MastersView.vue'
import ScheduleView from '../views/ScheduleView.vue'
import ServicesView from '../views/ServicesView.vue'

// Разделы с готовым бэкендом получают свои view, остальное — заглушка.
// Важно: у child-роутов vue-router 4.6 обязан быть компонент (или name/redirect),
// иначе они не регистрируются в матчере и любой /app/* матчится catch-all'ом
// с бесконечным redirect-циклом
const sectionComponents = {
  dashboard: PlaceholderSection,
  bookings: BookingsView,
  masters: MastersView,
  services: ServicesView,
  schedule: ScheduleView,
  business: BusinessView,
  'my-bookings': PlaceholderSection,
}

const appChildren = Object.entries(sectionComponents).map(([path, component]) => ({
  path,
  component,
  // meta заполняется из nav.js ниже
  meta: {},
}))

const router = createRouter({
  history: createWebHistory(import.meta.env.BASE_URL),
  routes: [
    { path: '/login', name: 'login', component: LoginView },
    {
      path: '/app',
      component: () => import('../views/InternalView.vue'),
      children: appChildren,
    },
    { path: '/', name: 'home', redirect: () => (authStore.me ? firstSectionPath(authStore.me.role) : '/login') },
    { path: '/:pathMatch(.*)*', redirect: '/' },
  ],
})

// Подтягиваем title/roles из nav.js в route.meta
import { SECTIONS } from '../nav'
for (const route of router.getRoutes()) {
  const section = SECTIONS.find((s) => s.path === route.path)
  if (section) {
    route.meta.title = section.title
    route.meta.roles = section.roles
    route.meta.name = section.name
  }
}

router.beforeEach((to) => {
  // Не залогинен — только страница входа
  if (!authStore.me) {
    return to.name === 'login' ? true : '/login'
  }
  if (to.name === 'login') {
    return firstSectionPath(authStore.me.role)
  }
  // Свой раздел или раздел своей роли
  if (to.meta.roles) {
    if (!to.meta.roles.includes(authStore.me.role)) {
      return firstSectionPath(authStore.me.role)
    }
    return true
  }
  // /app без вложенного пути → первый раздел своей роли
  if (to.path === '/app') {
    return firstSectionPath(authStore.me.role)
  }
  return true
})

export default router
