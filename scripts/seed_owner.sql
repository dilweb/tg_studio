-- ============================================================================
-- Скрипт для регистрации владельца (owner), создания бизнеса и мастера.
-- Запуск: PGPASSWORD=secret psql -h localhost -p 6005 -U tg_studio -d tg_studio -f scripts/seed_owner.sql
--
-- Кастомизация: задай переменную owner_id перед запуском:
--   psql ... -v owner_id=22813371488 -f scripts/seed_owner.sql
-- ============================================================================

-- Если переменная не задана через -v, используем значение по умолчанию
\set owner_id 22813371488

BEGIN;

-- 1. Создаём пользователя-владельца (если ещё не существует)
INSERT INTO users (
    telegram_id,
    first_name,
    role,
    password_hash,
    is_active,
    is_email_verified,
    created_at
)
VALUES (
    :owner_id,
    'Owner',
    'owner',
    '$2b$12$BkjosFG5ezTMY5gPKp8WTuPmJxmylMvwRjZ1YLYlqc2CaQIj2.2RW',  -- bcrypt('123')
    TRUE,
    TRUE,
    NOW()
)
ON CONFLICT (telegram_id) DO NOTHING;

-- 2. Создаём бизнес для этого владельца (если ещё не существует)
INSERT INTO businesses (
    owner_id,
    owner_telegram_id,
    name,
    description,
    is_active,
    created_at
)
SELECT
    u.id,
    :owner_id,
    'TG Studio',
    'Тату-студия',
    TRUE,
    NOW()
FROM users u
WHERE u.telegram_id = :owner_id
  AND NOT EXISTS (
    SELECT 1 FROM businesses b WHERE b.owner_telegram_id = :owner_id
);

-- 3. Создаём мастера (если ещё не существует)
INSERT INTO users (
    telegram_id,
    first_name,
    role,
    password_hash,
    is_active,
    is_email_verified,
    created_at
)
SELECT
    :owner_id + 1,
    'Master',
    'master',
    '$2b$12$BkjosFG5ezTMY5gPKp8WTuPmJxmylMvwRjZ1YLYlqc2CaQIj2.2RW',  -- bcrypt('123')
    TRUE,
    TRUE,
    NOW()
WHERE NOT EXISTS (
    SELECT 1 FROM users u
    WHERE u.telegram_id = :owner_id + 1
      AND u.role = 'master'
);

-- Создаём запись мастера, привязанную к бизнесу
INSERT INTO masters (
    business_id,
    user_id,
    telegram_id,
    full_name,
    description,
    is_active,
    password_hash
)
SELECT
    b.id,
    u.id,
    u.telegram_id,
    'Master Tattoo',
    'Главный мастер студии',
    TRUE,
    u.password_hash
FROM users u
JOIN businesses b ON b.owner_telegram_id = :owner_id
WHERE u.telegram_id = :owner_id + 1
  AND u.role = 'master'
  AND NOT EXISTS (
    SELECT 1 FROM masters m WHERE m.telegram_id = :owner_id + 1
);

-- 4. Проверка результата
SELECT
    u.telegram_id,
    u.role,
    u.first_name,
    b.name AS business_name,
    m.full_name AS master_name
FROM users u
LEFT JOIN businesses b ON b.owner_id = u.id
LEFT JOIN masters m ON m.user_id = u.id
WHERE u.telegram_id IN (:owner_id, :owner_id + 1)
ORDER BY u.role;

COMMIT;
\q