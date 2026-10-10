<script setup>
import { onMounted, reactive, ref } from 'vue'

import { api } from '../api/client'
const masters = ref([])
const loading = ref(true)
const error = ref('')
const saving = ref(false)
const showForm = ref(false)
// null → создание, число → правка существующей
const editingId = ref(null)
// master_id → { link, payload }: сгенерированные ссылки регистрации
const regLinks = reactive({})
const copiedId = ref(null)

const form = reactive({
  full_name: '',
  description: '',
  telegram_id: '',
  specializations: [], // стили мастера; пусто = универсал (любой стиль)
})
// Справочник стилей — тот же, что в работах (/api/tattoo/options)
const styleOptions = ref([])

async function load() {
  loading.value = true
  error.value = ''
  try {
    const [loaded, options] = await Promise.all([
      api.get('/api/admin/masters'),
      api.get('/api/tattoo/options'),
    ])
    masters.value = loaded
    styleOptions.value = options.styles
  } catch (err) {
    error.value = err.detail ?? err.message
  } finally {
    loading.value = false
  }
}

// «Лайнворк (Linework)» → «Лайнворк», «Графика / Гравюра (Engraving)» → «Графика»
function shortStyle(style) {
  return style.split(' (')[0].split(' / ')[0]
}

function openCreate() {
  editingId.value = null
  Object.assign(form, {
    full_name: '',
    description: '',
    telegram_id: '',
    specializations: [],
  })
  error.value = ''
  showForm.value = true
}

function openEdit(m) {
  editingId.value = m.id
  Object.assign(form, {
    full_name: m.full_name,
    description: m.description ?? '',
    telegram_id: m.telegram_id === null || m.telegram_id === undefined ? '' : String(m.telegram_id),
    specializations: [...(m.specializations ?? [])],
  })
  error.value = ''
  showForm.value = true
}

async function submit() {
  if (!form.full_name.trim()) {
    error.value = 'Введите имя мастера'
    return
  }
  // При создании Telegram ID обязателен: без него мастер не войдёт в миниапп
  if (editingId.value === null && !form.telegram_id.trim()) {
    error.value = 'Укажите Telegram ID мастера — без него он не войдёт в миниапп'
    return
  }
  if (form.telegram_id.trim() && !/^\d+$/.test(form.telegram_id.trim())) {
    error.value = 'Telegram ID — только цифры'
    return
  }

  saving.value = true
  error.value = ''
  notice.value = ''
  try {
    const tg = form.telegram_id.trim() ? Number(form.telegram_id.trim()) : null
    if (editingId.value === null) {
      const created = await api.post('/api/admin/masters', {
        full_name: form.full_name.trim(),
        description: form.description.trim(),
        telegram_id: tg,
        specializations: [...form.specializations],
      })
      if (created.google_calendar_id) {
        notice.value = `Мастер добавлен, календарь создан. Нажми «Добавить себе» в строке ${created.full_name}, чтобы он появился в твоём Google Calendar.`
      } else {
        notice.value = 'Мастер добавлен.'
      }
    } else {
      // PATCH не принимает null для telegram_id (null = «не менять»),
      // поэтому пустое поле просто не отправляем
      const patch = {
        full_name: form.full_name.trim(),
        description: form.description.trim(),
        // Специализации отправляем всегда: снятые галочки — это «универсал»
        specializations: [...form.specializations],
      }
      if (tg !== null) patch.telegram_id = tg
      await api.patch(`/api/admin/masters/${editingId.value}`, patch)
    }
    showForm.value = false
    await load()
  } catch (err) {
    error.value = err.detail ?? err.message
  } finally {
    saving.value = false
  }
}

async function toggleActive(m) {
  error.value = ''
  notice.value = ''
  try {
    await api.patch(`/api/admin/masters/${m.id}`, { is_active: !m.is_active })
    await load()
  } catch (err) {
    error.value = err.detail ?? err.message
  }
}

// ── Удаление мастера (мягкое): из списков пропадает, запись в БД остаётся ──
const showDeleted = ref(false)
const deletingId = ref(null)

async function loadMasters() {
  try {
    masters.value = await api.get(
      `/api/admin/masters${showDeleted.value ? '?include_deleted=true' : ''}`,
    )
  } catch (err) {
    error.value = err.detail ?? err.message
  }
}

async function deleteMaster(m) {
  if (
    !confirm(
      `Удалить мастера ${m.full_name}? Он пропадёт из списков и из бота, его календарь в Google будет удалён. ` +
        'Работы и платежи останутся в базе (можно посмотреть, включив «показать удалённых»).',
    )
  ) {
    return
  }
  error.value = ''
  notice.value = ''
  deletingId.value = m.id
  try {
    await api.del(`/api/admin/masters/${m.id}`)
    notice.value = `Мастер ${m.full_name} удалён. История работ и платежей сохранена в базе.`
    await loadMasters()
  } catch (err) {
    error.value = err.detail ?? err.message
  } finally {
    deletingId.value = null
  }
}

async function restoreMaster(m) {
  error.value = ''
  notice.value = ''
  try {
    await api.post(`/api/admin/masters/${m.id}/restore`)
    notice.value = `Мастер ${m.full_name} возвращён в список.`
    await loadMasters()
  } catch (err) {
    error.value = err.detail ?? err.message
  }
}

async function makeRegLink(m) {
  error.value = ''
  try {
    const res = await api.post(`/api/admin/masters/${m.id}/registration-link`)
    regLinks[m.id] = res
  } catch (err) {
    error.value = err.detail ?? err.message
  }
}

const creatingCalendarId = ref(null)
// подсказка после успешных действий (создание календаря, ссылка входа)
const notice = ref('')

// ── Портфолио мастеров ────────────────────────────────────────────────────
// Загружается владельцем, показывается клиентам (бот агент + публичная ссылка).
// master_id → true: блок портфолио раскрыт
const portfolioOpen = reactive({})
const uploadingPortfolioId = ref(null)

function togglePortfolio(m) {
  portfolioOpen[m.id] = !portfolioOpen[m.id]
}

function portfolioUrl(m, f) {
  // url с бэка абсолютный (API_PUBLIC_URL) — если пусто, строим относительную
  if (f.url) return f.url
  return `/api/public/masters/${m.id}/portfolio/${f.id}`
}

// ── Аватар мастера (фото профиля) ────────────────────────────────────────
const uploadingAvatarId = ref(null)

function avatarUrl(m) {
  if (m.avatar_url) return m.avatar_url
  return `/api/public/masters/${m.id}/avatar`
}

function pickAvatar(m, event) {
  const file = event.target.files?.[0] ?? null
  event.target.value = ''
  if (file) uploadAvatar(m, file)
}

async function uploadAvatar(m, file) {
  error.value = ''
  uploadingAvatarId.value = m.id
  try {
    const fd = new FormData()
    fd.append('file', file)
    const updated = await api.postForm(`/api/admin/masters/${m.id}/avatar`, fd)
    m.avatar_url = updated.avatar_url
    notice.value = `Фото профиля ${m.full_name} обновлено.`
  } catch (err) {
    error.value = err.detail ?? err.message
  } finally {
    uploadingAvatarId.value = null
  }
}

async function removeAvatar(m) {
  error.value = ''
  try {
    await api.del(`/api/admin/masters/${m.id}/avatar`)
    m.avatar_url = null
  } catch (err) {
    error.value = err.detail ?? err.message
  }
}

function pickPortfolioFile(m, event) {
  const file = event.target.files?.[0] ?? null
  event.target.value = ''
  if (file) uploadPortfolio(m, file)
}

async function uploadPortfolio(m, file) {
  error.value = ''
  uploadingPortfolioId.value = m.id
  try {
    const fd = new FormData()
    fd.append('file', file)
    const updated = await api.postForm(`/api/admin/masters/${m.id}/portfolio`, fd)
    m.portfolio = updated.portfolio
    notice.value = `Фото добавлено в портфолио ${m.full_name}.`
  } catch (err) {
    error.value = err.detail ?? err.message
  } finally {
    uploadingPortfolioId.value = null
  }
}

async function deletePortfolioFile(m, f) {
  error.value = ''
  try {
    await api.del(`/api/admin/masters/${m.id}/portfolio/${f.id}`)
    m.portfolio = (m.portfolio ?? []).filter((x) => x.id !== f.id)
  } catch (err) {
    error.value = err.detail ?? err.message
  }
}

async function createCalendar(m) {
  error.value = ''
  notice.value = ''
  creatingCalendarId.value = m.id
  try {
    const res = await api.post(`/api/admin/google-calendar/masters/${m.id}/calendar`)
    if (res.share_error) {
      error.value = `Календарь создан, но открыть доступ не удалось: ${res.share_error}`
    } else {
      notice.value = `Календарь создан. Нажми «Добавить себе» в строке ${m.full_name}, чтобы он появился в твоём Google Calendar.`
    }
    await load()
  } catch (err) {
    error.value = err.detail ?? err.message
  } finally {
    creatingCalendarId.value = null
  }
}

async function copyLink(m) {
  const link = regLinks[m.id]?.link
  if (!link) return
  try {
    await navigator.clipboard.writeText(link)
    copiedId.value = m.id
    setTimeout(() => {
      copiedId.value = null
    }, 1500)
  } catch {
    // clipboard недоступен — ссылка всё равно видна и выделяется вручную
  }
}

onMounted(load)
</script>

<template>
  <div>
    <div v-if="error" class="error-box">{{ error }}</div>
    <div v-else-if="notice" class="success-box">{{ notice }}</div>

    <div class="toolbar">
      <button class="btn btn-primary btn-sm" @click="openCreate">+ Добавить мастера</button>
      <label class="checkbox-row" style="margin-left: auto">
        <input v-model="showDeleted" type="checkbox" @change="loadMasters" />
        <span>показать удалённых</span>
      </label>
    </div>

    <form v-if="showForm" class="card" style="margin-bottom: 16px" @submit.prevent="submit">
      <div class="form-grid">
        <div class="field">
          <label for="m-name">Имя</label>
          <input id="m-name" v-model="form.full_name" autocomplete="off" />
        </div>
        <div class="field">
          <label for="m-tg">Telegram ID{{ editingId === null ? ' (обязателен)' : '' }}</label>
          <input
            id="m-tg"
            v-model="form.telegram_id"
            :placeholder="editingId !== null ? 'не менять, если пусто' : 'например 228553615'"
            inputmode="numeric"
            autocomplete="off"
          />
          <div v-if="editingId === null" class="muted" style="font-size: 12px; margin-top: 4px">
            По этому id мастер войдёт в миниапп — env и деплой не нужны
          </div>
        </div>
      </div>
      <div class="field">
        <label for="m-desc">Описание</label>
        <textarea id="m-desc" v-model="form.description" rows="2"></textarea>
      </div>

      <div class="field">
        <label>Специализации (не отметишь — считается универсалом, берёт любые стили)</label>
        <div class="spec-grid">
          <label v-for="style in styleOptions" :key="style" class="checkbox-row">
            <input type="checkbox" :value="style" v-model="form.specializations" />
            <span>{{ shortStyle(style) }}</span>
          </label>
        </div>
      </div>

      <div style="display: flex; gap: 10px">
        <button class="btn btn-primary" type="submit" :disabled="saving">
          {{ saving ? 'Сохраняем…' : editingId === null ? 'Создать' : 'Сохранить' }}
        </button>
        <button class="btn btn-ghost" type="button" @click="showForm = false">Отмена</button>
      </div>
    </form>

    <div v-if="loading" class="muted">Загрузка…</div>

    <div v-else-if="!masters.length" class="card">
      <div class="empty-state">
        <div class="empty-title">Мастеров пока нет</div>
        <div>Добавьте первого мастера — записи появятся после этого</div>
        <button class="btn btn-primary btn-sm" @click="openCreate">+ Добавить мастера</button>
      </div>
    </div>

    <div v-else class="table-wrap">
      <table class="table">
        <thead>
          <tr>
            <th>Мастер</th>
            <th>Telegram</th>
            <th>Календарь</th>
            <th>Статус</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          <template v-for="m in masters" :key="m.id">
            <tr :class="{ 'deleted-row': m.deleted_at }">
              <td>
                <div style="display: flex; align-items: center; gap: 10px">
                  <img
                    v-if="m.avatar_url"
                    :src="avatarUrl(m)"
                    :alt="m.full_name"
                    class="master-avatar"
                  />
                  <span v-else class="master-avatar master-avatar-empty">
                    {{ m.full_name.slice(0, 1).toUpperCase() }}
                  </span>
                  <div>{{ m.full_name }}</div>
                </div>
                <div v-if="m.description" class="muted" style="font-size: 12px">{{ m.description }}</div>
                <div v-if="m.specializations?.length" style="margin-top: 4px; display: flex; flex-wrap: wrap; gap: 4px">
                  <span v-for="s in m.specializations" :key="s" class="badge badge-muted">
                    {{ shortStyle(s) }}
                  </span>
                </div>
                <div v-else class="muted" style="font-size: 12px; margin-top: 4px">универсал</div>
              </td>
              <td>
                <template v-if="m.telegram_id">
                  <div>{{ m.telegram_id }}</div>
                  <div class="badge badge-green" style="margin-top: 4px">привязан</div>
                </template>
                <template v-else>
                  <div class="muted">не привязан</div>
                  <button class="btn btn-sm" style="margin-top: 4px" @click="makeRegLink(m)">
                    Ссылка для входа
                  </button>
                </template>
              </td>
              <td>
                <template v-if="m.google_calendar_id">
                  <div><span class="badge badge-green">создан</span></div>
                  <a
                    class="btn btn-sm"
                    style="margin-top: 4px"
                    :href="m.calendar_add_url"
                    target="_blank"
                    rel="noopener"
                  >
                    Добавить себе
                  </a>
                </template>
                <button
                  v-else-if="!m.deleted_at"
                  class="btn btn-sm"
                  :disabled="creatingCalendarId === m.id"
                  @click="createCalendar(m)"
                >
                  {{ creatingCalendarId === m.id ? 'Создаём…' : 'Создать календарь' }}
                </button>
                <div v-else class="muted" style="font-size: 12px">—</div>
              </td>
              <td>
                <span
                  v-if="m.deleted_at"
                  class="badge badge-muted"
                  title="Запись осталась в базе, мастер скрыт из списков и бота"
                >
                  Удалён
                </span>
                <span v-else class="badge" :class="m.is_active ? 'badge-green' : 'badge-muted'">
                  {{ m.is_active ? 'Активен' : 'Выключен' }}
                </span>
              </td>
              <td style="text-align: right; white-space: nowrap">
                <template v-if="!m.deleted_at">
                  <button class="btn btn-sm" @click="openEdit(m)">Изменить</button>
                  <button class="btn btn-sm" @click="togglePortfolio(m)">
                    Портфолио{{ m.portfolio?.length ? ` (${m.portfolio.length})` : '' }}
                  </button>
                  <button class="btn btn-sm btn-ghost" @click="toggleActive(m)">
                    {{ m.is_active ? 'Выключить' : 'Включить' }}
                  </button>
                  <button
                    class="btn btn-sm btn-ghost delete-btn"
                    :disabled="deletingId === m.id"
                    @click="deleteMaster(m)"
                  >
                    {{ deletingId === m.id ? 'Удаляем…' : 'Удалить' }}
                  </button>
                </template>
                <button v-else class="btn btn-sm" @click="restoreMaster(m)">Восстановить</button>
              </td>
            </tr>
            <tr v-if="portfolioOpen[m.id]">
              <td colspan="5" style="background: rgba(108, 99, 255, 0.05)">
                <div style="margin-top: 4px">
                  <label class="btn btn-sm btn-primary" style="cursor: pointer">
                    {{ uploadingPortfolioId === m.id ? 'Загружаем…' : '+ Добавить фото' }}
                    <input
                      type="file"
                      accept="image/*"
                      hidden
                      :disabled="uploadingPortfolioId === m.id"
                      @change="pickPortfolioFile(m, $event)"
                    />
                  </label>
                  <span class="muted" style="font-size: 12px; margin-left: 8px">
                    Фото увидят клиенты: агент записи шлёт ссылки, мастер показывается в миниаппе
                  </span>
                </div>

                <div
                  style="display: flex; align-items: center; gap: 12px; margin-top: 14px; padding-top: 12px; border-top: 1px solid var(--border)"
                >
                  <img v-if="m.avatar_url" :src="avatarUrl(m)" :alt="m.full_name" class="master-avatar" />
                  <span v-else class="master-avatar master-avatar-empty">
                    {{ m.full_name.slice(0, 1).toUpperCase() }}
                  </span>
                  <div>
                    <label class="btn btn-sm" style="cursor: pointer">
                      {{ uploadingAvatarId === m.id ? 'Загружаем…' : 'Фото профиля' }}
                      <input
                        type="file"
                        accept="image/*"
                        hidden
                        :disabled="uploadingAvatarId === m.id"
                        @change="pickAvatar(m, $event)"
                      />
                    </label>
                    <button
                      v-if="m.avatar_url"
                      class="btn btn-sm btn-ghost"
                      style="margin-left: 8px"
                      @click="removeAvatar(m)"
                    >
                      Убрать
                    </button>
                    <div class="muted" style="font-size: 12px; margin-top: 4px">
                      Аватар показывается клиентам рядом с именем мастера
                    </div>
                  </div>
                </div>
                <div
                  v-if="m.portfolio?.length"
                  style="display: flex; flex-wrap: wrap; gap: 10px; margin-top: 12px"
                >
                  <div
                    v-for="f in m.portfolio"
                    :key="f.id"
                    class="portfolio-thumb"
                  >
                    <a :href="portfolioUrl(m, f)" target="_blank" rel="noopener">
                      <img :src="portfolioUrl(m, f)" :alt="f.original_name" loading="lazy" />
                    </a>
                    <button
                      class="portfolio-delete"
                      type="button"
                      title="Удалить фото"
                      @click="deletePortfolioFile(m, f)"
                    >✕</button>
                  </div>
                </div>
                <div v-else class="muted" style="font-size: 12.5px; margin-top: 10px">
                  Фото пока нет — добавь работы мастера
                </div>
              </td>
            </tr>
            <tr v-if="regLinks[m.id]">
              <td colspan="5" style="background: rgba(108, 99, 255, 0.05)">
                <div class="link-box" style="margin-top: 0">
                  <span class="link-text">
                    {{ regLinks[m.id].link ?? regLinks[m.id].payload }}
                  </span>
                  <button
                    v-if="regLinks[m.id].link"
                    class="btn btn-sm"
                    type="button"
                    @click="copyLink(m)"
                  >
                    {{ copiedId === m.id ? 'Скопировано' : 'Копировать' }}
                  </button>
                </div>
                <div class="muted" style="font-size: 12px; margin-top: 8px">
                  Отправь эту ссылку мастеру — по ней он откроет миниапп и привяжет свой Telegram.
                  Ссылка одноразовая: при генерации новой старая перестаёт работать.
                </div>
              </td>
            </tr>
          </template>
        </tbody>
      </table>
    </div>
  </div>
</template>

<style scoped>
.master-avatar {
  width: 40px;
  height: 40px;
  border-radius: 50%;
  object-fit: cover;
  flex-shrink: 0;
  border: 1px solid var(--border);
}

.master-avatar-empty {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  background: var(--surface2);
  color: var(--muted);
  font-size: 15px;
  font-weight: 600;
}

/* Удалённый мастер: приглушён (запись осталась в БД) */
.deleted-row td {
  opacity: 0.55;
}

/* Чекбоксы специализаций: 2 колонки, без наложения на подписи */
.spec-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(170px, 1fr));
  gap: 8px 14px;
  margin-top: 4px;
}

.delete-btn:hover {
  color: #c82828;
}

.portfolio-thumb {
  position: relative;
  width: 110px;
  height: 110px;
  border-radius: 8px;
  overflow: hidden;
  border: 1px solid var(--border);
}

.portfolio-thumb img {
  width: 100%;
  height: 100%;
  object-fit: cover;
  display: block;
}

.portfolio-delete {
  position: absolute;
  top: 4px;
  right: 4px;
  width: 22px;
  height: 22px;
  border-radius: 50%;
  border: none;
  background: rgba(0, 0, 0, 0.55);
  color: #fff;
  cursor: pointer;
  font-size: 11px;
  line-height: 1;
  display: flex;
  align-items: center;
  justify-content: center;
}

.portfolio-delete:hover {
  background: rgba(200, 40, 40, 0.85);
}
</style>
