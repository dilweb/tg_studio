# TG Studio — Система записи через Telegram

CRM backend for tattoo studios — scheduling, customers, analytics.

## Стек

| Слой | Технология |
|------|-----------|
| Telegram Bot | aiogram 3 |
| API Backend | FastAPI + Uvicorn |
| База данных | PostgreSQL + SQLAlchemy 2.0 (async) + Alembic |
| Очередь задач | Redis + Celery |
| Контейнеры | Docker Compose + Nginx |

## Быстрый старт

### 1. Скопировать конфиг

```bash
cp .env.example .env
```

Заполнить в `.env`:
- `BOT_TOKEN` — токен из [@BotFather](https://t.me/BotFather)
- `MINIAPP_URL` — URL где будет хоститься фронтенд (должен быть HTTPS)

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
INSERT INTO services (name, price) VALUES ('Тату', 5000);
INSERT INTO master_services (master_id, service_id) VALUES (1, 1);
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
                                    └─ GET /api/health
```

## API Endpoints

| Метод | Путь | Описание |
|-------|------|----------|
| GET | `/api/slots/masters` | Список активных мастеров |
| GET | `/api/slots/masters/{id}/services` | Услуги мастера |
| GET | `/api/slots/available?master_id=1&day=2026-03-10` | Свободные слоты |
| GET | `/api/health` | Healthcheck |

## Команды бота

| Команда | Описание |
|---------|----------|
| `/start` | Открыть Mini App для записи |
