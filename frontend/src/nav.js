// Разделы внутреннего интерфейса по ролям.

export const SECTIONS = [
  { name: 'dashboard', path: '/app/dashboard', title: 'Дашборд', roles: ['owner'], icon: 'grid' },
  { name: 'masters', path: '/app/masters', title: 'Мастера', roles: ['owner'], icon: 'users' },
  { name: 'works', path: '/app/works', title: 'Работы', roles: ['owner', 'master'], icon: 'calendar' },
  { name: 'supplies', path: '/app/supplies', title: 'Склад', roles: ['owner', 'master'], icon: 'box' },
  { name: 'chats', path: '/app/chats', title: 'Чаты', roles: ['owner', 'master'], icon: 'chat' },
  { name: 'business', path: '/app/business', title: 'Бизнес', roles: ['owner'], icon: 'briefcase' },
  { name: 'ai', path: '/app/ai', title: 'AI-ассистент', roles: ['owner'], icon: 'ai' },
]

export function sectionsFor(role) {
  return SECTIONS.filter((s) => s.roles.includes(role))
}

export function firstSectionPath(role) {
  return sectionsFor(role)[0]?.path ?? '/login'
}
