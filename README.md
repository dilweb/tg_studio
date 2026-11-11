# TG Studio — CRM коворкинга

Студия сдаёт время 4 рабочих мест независимым мастерам: брони с 100% предоплатой,
цены по правилам (утро/вечер, будни/выходные, праздники, скидки), баланс мастера,
админы с апсейлами, склад и продажи, дашборд и Excel-отчёты для владельца.
План и модель — в [docs/coworking](docs/coworking/00-overview.md).

## Стек

| Слой | Технология |
|------|-----------|
| API | FastAPI + Uvicorn |
| База данных | PostgreSQL + SQLAlchemy 2.0 (async) + Alembic |
| Очередь задач | Redis + Celery (истечение hold'ов броней, письма) |
| Фронтенд | Vue (Telegram Mini App / веб), `frontend/` |
| Платежи | ApiPay (Kaspi) или ручное подтверждение |
| Зависимости | uv |
| Контейнеры | Docker Compose + Nginx |

Telegram-бота как процесса нет: Bot API используется только для исходящих
уведомлений (`modules/notify.py`) и проверки `initData` миниаппа (`BOT_TOKEN`).

## Быстрый старт

```bash
# заполнить .env: BOT_TOKEN, MINIAPP_URL (HTTPS), JWT_SECRET_KEY, ALLOWED_USERS
docker compose up -d
```

Миграции применяются при старте сервиса `api` (`RUN_MIGRATIONS=true`).

## Разработка и тесты

```bash
uv sync --group dev
uv run pytest            # SQLite in-memory, Postgres не нужен
uv run alembic upgrade head
```

Тесты `test_coworking_postgres`-типа и миграции требуют реальный Postgres.

## API

Swagger — `/api/docs` (только при `REQUIRE_HTTPS=false`). Основные разделы:
`/api/seats`, `/api/bookings`, `/api/pricing/quote`, `/api/price-rules`,
`/api/me/balance`, `/api/payments`, `/api/admins`, `/api/shifts`,
`/api/dashboard`, `/api/reports/*` (+ `revenue.xlsx`), `/api/health`.
