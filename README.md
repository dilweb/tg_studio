# TG Studio — Система записи через Telegram

Telegram Mini App + Bot для записи клиентов на сеанс с предоплатой через Kaspi Pay.

## Стек

| Слой | Технология |
|------|-----------|
| Telegram Bot | aiogram 3 |
| API Backend | FastAPI + Uvicorn |
| База данных | PostgreSQL + SQLAlchemy 2.0 (async) + Alembic |
| Очередь задач | Redis + Celery |
| Оплата | Kaspi Pay API |
| Контейнеры | Docker Compose + Nginx |

## Быстрый старт

### 1. Скопировать конфиг

```bash
cp .env.example .env
```

Заполнить в `.env`:
- `BOT_TOKEN` — токен из [@BotFather](https://t.me/BotFather)
- `MINIAPP_URL` — URL где будет хоститься фронтенд (должен быть HTTPS)
- `KASPI_MERCHANT_ID`, `KASPI_API_KEY` — данные из личного кабинета Kaspi Business

### 2. Запустить

```bash
docker compose up -d
```

Миграции применяются автоматически при старте (сервис `migrate`). Запустить миграции вручную:
```bash
docker compose run migrate
```

### 3. Добавить данные

Подключиться к postgres и добавить мастеров, услуги и временные слоты:

```sql
INSERT INTO masters (full_name, telegram_id) VALUES ('Алия Нурова', 123456789);
INSERT INTO services (name, duration_minutes, price, prepayment_amount) VALUES ('Маникюр', 60, 5000, 1000);
INSERT INTO master_services (master_id, service_id) VALUES (1, 1);
-- Слоты можно добавлять скриптом или через будущую admin-панель
```

## Архитектура

```
Клиент (Telegram)
    │
    ├─ /start → aiogram Bot → кнопка "Записаться" (открывает Mini App)
    │
    └─ Mini App (React/Vue) ──→ FastAPI Backend
                                    ├─ GET /api/slots/masters
                                    ├─ GET /api/slots/available
                                    ├─ POST /api/bookings  ──→ Kaspi Pay API
                                    └─ POST /api/kaspi/callback (webhook)
                                            │
                                            └─ Celery task: уведомление клиенту + мастеру
```

## API Endpoints

| Метод | Путь | Описание |
|-------|------|----------|
| GET | `/api/slots/masters` | Список активных мастеров |
| GET | `/api/slots/masters/{id}/services` | Услуги мастера |
| GET | `/api/slots/available?master_id=1&day=2026-03-10` | Свободные слоты |
| POST | `/api/bookings` | Создать бронь + получить ссылку Kaspi |
| GET | `/api/bookings/{id}` | Статус брони |
| POST | `/api/kaspi/callback` | Webhook от Kaspi Pay |
| GET | `/api/health` | Healthcheck |

## Команды бота

| Команда | Описание |
|---------|----------|
| `/start` | Открыть Mini App для записи |
| `/bookings` | Список записей (только для мастеров) |

## Фазы разработки

- [x] **Фаза 1** — MVP: БД, API, Kaspi Pay, уведомления
- [ ] **Фаза 2** — Напоминания (Celery beat), отмена/перенос записи
- [ ] **Фаза 3** — Frontend (Mini App), admin-панель
- [ ] **Фаза 4** — CRM: история клиентов, аналитика, мультимастер
