# Сборка Telegram Mini App: node собирает vite-бандл, итоговый образ —
# nginx с запечённым dist и конфигом. Раньше nginx монтировал ./frontend/dist
# с хоста, а собирался он только вручную (npm run build) — в прод уезжал
# устаревший бандл. Теперь фронт — часть `docker compose build`: не пересобрал
# образ nginx — раздаёшь старый код, как и с api/bot/worker/beat.
#
# Контекст сборки — корень репо (frontend/ + docker/):
#   docker compose build nginx
FROM node:22-alpine AS build
WORKDIR /build
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM nginx:alpine
COPY docker/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /build/dist /usr/share/nginx/html
