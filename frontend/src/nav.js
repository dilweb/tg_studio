// Разделы внутреннего интерфейса по ролям.

export const SECTIONS = [
  // Главная владельца — обзор студии; остальные роли начинают с календаря.
  { name: 'dashboard', path: '/app/dashboard', title: 'Дашборд', roles: ['owner'], icon: 'grid' },
  { name: 'reports', path: '/app/reports', title: 'Отчёты', roles: ['owner'], icon: 'calendar' },
  { name: 'calendar', path: '/app/calendar', title: 'Календарь', roles: ['owner', 'master', 'admin'], icon: 'calendar' },
  { name: 'balance', path: '/app/balance', title: 'Баланс', roles: ['owner', 'master', 'admin'], icon: 'wallet' },
  { name: 'desk', path: '/app/desk', title: 'Админ', roles: ['admin', 'owner'], icon: 'grid' },
  { name: 'shifts', path: '/app/shifts', title: 'Смены', roles: ['admin', 'owner'], icon: 'clock' },
  { name: 'upsells', path: '/app/upsells', title: 'Апсейлы', roles: ['admin', 'owner'], icon: 'wallet' },
  { name: 'seats', path: '/app/seats', title: 'Настройка мест', roles: ['owner'], icon: 'grid' },
  { name: 'masters', path: '/app/masters', title: 'Мастера', roles: ['owner'], icon: 'users' },
  { name: 'admins', path: '/app/admins', title: 'Администраторы', roles: ['owner'], icon: 'users' },
  { name: 'stock', path: '/app/stock', title: 'Склад', roles: ['owner', 'master', 'admin'], icon: 'box' },
  { name: 'checkout', path: '/app/checkout', title: 'Касса', roles: ['owner', 'master', 'admin'], icon: 'wallet' },
  { name: 'business', path: '/app/business', title: 'Бизнес', roles: ['owner'], icon: 'briefcase' },
]

export function sectionsFor(role) {
  return SECTIONS.filter((s) => s.roles.includes(role))
}

export function firstSectionPath(role) {
  return sectionsFor(role)[0]?.path ?? '/login'
}
