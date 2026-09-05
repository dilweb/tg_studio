// Подписи и цвета статусов для интерфейса.

export const BOOKING_STATUS = {
  pending: { label: 'Ожидает оплаты', tone: 'yellow' },
  confirmed: { label: 'Подтверждена', tone: 'blue' },
  in_progress: { label: 'В работе', tone: 'accent' },
  completed: { label: 'Завершена', tone: 'green' },
  cancelled: { label: 'Отменена', tone: 'red' },
}

export const SERVICE_TYPE = {
  appointment: 'По записи',
  project: 'Проект',
}

export const STATUS_FILTERS = [
  { value: '', label: 'Все статусы' },
  { value: 'pending', label: 'Ожидает оплаты' },
  { value: 'confirmed', label: 'Подтверждена' },
  { value: 'in_progress', label: 'В работе' },
  { value: 'completed', label: 'Завершена' },
  { value: 'cancelled', label: 'Отменена' },
]
