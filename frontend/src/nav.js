// Разделы внутреннего интерфейса по ролям.

export const SECTIONS = [
  { name: 'dashboard', path: '/app/dashboard', title: 'Дашборд', roles: ['owner'], icon: 'grid' },
  { name: 'bookings', path: '/app/bookings', title: 'Записи', roles: ['owner'], icon: 'calendar' },
  { name: 'masters', path: '/app/masters', title: 'Мастера', roles: ['owner'], icon: 'users' },
  { name: 'chats', path: '/app/chats', title: 'Чаты', roles: ['owner', 'master'], icon: 'chat' },
  { name: 'business', path: '/app/business', title: 'Бизнес', roles: ['owner'], icon: 'briefcase' },
  { name: 'my-bookings', path: '/app/my-bookings', title: 'Мои записи', roles: ['master'], icon: 'calendar' },
]

export function sectionsFor(role) {
  return SECTIONS.filter((s) => s.roles.includes(role))
}

export function firstSectionPath(role) {
  return sectionsFor(role)[0]?.path ?? '/login'
}
