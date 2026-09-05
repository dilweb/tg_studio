// Разделы внутреннего интерфейса по ролям.

export const SECTIONS = [
  { name: 'dashboard', path: '/app/dashboard', title: 'Дашборд', roles: ['admin'], icon: 'grid' },
  { name: 'bookings', path: '/app/bookings', title: 'Записи', roles: ['admin'], icon: 'calendar' },
  { name: 'masters', path: '/app/masters', title: 'Мастера', roles: ['admin'], icon: 'users' },
  { name: 'services', path: '/app/services', title: 'Услуги', roles: ['admin'], icon: 'spark' },
  { name: 'schedule', path: '/app/schedule', title: 'Расписание', roles: ['admin', 'master'], icon: 'clock' },
  { name: 'business', path: '/app/business', title: 'Бизнес', roles: ['admin'], icon: 'briefcase' },
  { name: 'my-bookings', path: '/app/my-bookings', title: 'Мои записи', roles: ['master'], icon: 'calendar' },
]

export function sectionsFor(role) {
  return SECTIONS.filter((s) => s.roles.includes(role))
}

export function firstSectionPath(role) {
  return sectionsFor(role)[0]?.path ?? '/login'
}
