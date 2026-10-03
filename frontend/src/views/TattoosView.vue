<script setup>
import { computed, onMounted, reactive, ref, watch } from 'vue'

import { api } from '../api/client'
import { authStore } from '../store/auth'
import { compressImage } from '../utils/images'

const isOwner = computed(() => authStore.me?.role === 'owner')

const works = ref([])
const clients = ref([])
const masters = ref([])
// Справочники с бэка: GET /api/tattoo/options
const styleOptions = ref([])
const placementOptions = ref([])
const complexityOptions = ref(['низкая', 'средняя', 'высокая'])
const loading = ref(true)
const error = ref('')
const notice = ref('')
const saving = ref(false)
const showForm = ref(false)

// Фильтр по мастеру (только владелец)
const masterFilter = ref('')

const STATUS_LABELS = {
  in_progress: 'В работе',
  completed: 'Завершена',
  cancelled: 'Отменена',
}

const form = reactive({
  master_id: '',
  client_id: '',
  // Новый клиент (пришёл не из Telegram) — вместо выбора из списка
  newClient: false,
  new_name: '',
  new_phone: '',
  new_instagram: '',
  new_note: '',
  size_length_cm: '',
  size_height_cm: '',
  complexity: '',
  style: '',
  placement: '',
  coverup: false,
  session_date: '',
  recommended_price: '',
  cost: '',
  is_final_session: false,
})

// Добавление сеанса к существующей работе: null → форма закрыта
const sessionWorkId = ref(null)
const sessionForm = reactive({
  session_date: '',
  recommended_price: '',
  cost: '',
  is_final_session: false,
})

// Редактирование работы: null → форма закрыта
const editWorkId = ref(null)
const editWorkForm = reactive({
  size_length_cm: '',
  size_height_cm: '',
  complexity: '',
  style: '',
  placement: '',
  status: 'in_progress',
})

// Редактирование сеанса: { workId, sessionId } | null
const editSession = ref(null)
const editSessionForm = reactive({
  session_date: '',
  recommended_price: '',
  cost: '',
  is_final_session: false,
})

// Выбранные фото в формах (FileList)
const sketchFiles = ref(null) // эскиз при создании работы
const resultFiles = ref(null) // результат при добавлении сеанса
// Файл-инпут «+ фото» у сеанса: { workId, sessionId } | null
const photoTarget = ref(null)

const activeMasters = computed(() => masters.value.filter((m) => m.is_active))

const clientName = (id) => clients.value.find((c) => c.id === id)?.full_name ?? `клиент #${id}`
const masterName = (id) => masters.value.find((m) => m.id === id)?.full_name ?? `мастер #${id}`

const money = (n) => `${new Intl.NumberFormat('ru-RU').format(n)} ₸`

function fmtDate(iso) {
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return iso
  return d.toLocaleString('ru-RU', {
    day: '2-digit',
    month: 'short',
    hour: '2-digit',
    minute: '2-digit',
  })
}

function toIso(localValue) {
  // datetime-local → ISO 8601 (UTC)
  return new Date(localValue).toISOString()
}

function toLocalInput(iso) {
  // ISO → значение datetime-local (в локальном времени браузера)
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return ''
  const pad = (n) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`
}

const workTotal = (w) => w.sessions.reduce((sum, s) => sum + Number(s.cost ?? 0), 0)

const sketchCount = computed(() => sketchFiles.value?.length ?? 0)
const resultCount = computed(() => resultFiles.value?.length ?? 0)
const isDifficultPlacement = computed(
  () => placementOptions.value.find((p) => p.value === form.placement)?.difficult ?? false,
)

// Рекомендуемая цена: пересчитывается по прайсу при изменении параметров
// работы и перезаписывает поле; после расчёта сумму можно править вручную
watch(
  () => [
    form.size_length_cm,
    form.size_height_cm,
    form.style,
    form.placement,
    form.coverup,
  ],
  async ([len, hgt, style, placement, coverup]) => {
    if (!len || !hgt || !style || !placement) return
    try {
      const params = new URLSearchParams({
        length_cm: len,
        height_cm: hgt,
        style,
        placement,
      })
      if (coverup) params.set('coverup', 'true')
      const res = await api.get(`/api/tattoo/price/estimate?${params}`)
      if (res.recommended_price != null) form.recommended_price = String(res.recommended_price)
    } catch {
      /* прайс недоступен — поле заполняется вручную */
    }
  },
)

/** Загрузить фото (сжав) в сеанс; возвращает число успешных. */
async function uploadSessionFiles(workId, sessionId, kind, fileList) {
  let ok = 0
  for (const f of Array.from(fileList ?? [])) {
    try {
      const fd = new FormData()
      fd.append('kind', kind)
      fd.append('file', await compressImage(f))
      await api.postForm(`/api/tattoo/works/${workId}/sessions/${sessionId}/files`, fd)
      ok += 1
    } catch (err) {
      error.value = `Фото «${f.name}» не загрузилось: ${err.detail ?? err.message}`
    }
  }
  return ok
}

function openPhotoPicker(work, session) {
  photoTarget.value = { workId: work.id, sessionId: session.id }
  // reset value: выбор того же файла заново должен срабатывать
  document.getElementById('session-photo-input').value = ''
  document.getElementById('session-photo-input').click()
}

async function onSessionPhotosPicked(event) {
  const target = photoTarget.value
  photoTarget.value = null
  if (!target || !event.target.files?.length) return
  await uploadSessionFiles(target.workId, target.sessionId, 'result', event.target.files)
  await load()
}

async function deletePhoto(f) {
  error.value = ''
  try {
    await api.del(`/api/tattoo/files/${f.id}`)
    await load()
  } catch (err) {
    error.value = err.detail ?? err.message
  }
}

async function load() {
  loading.value = true
  error.value = ''
  try {
    const jobs = [
      api.get('/api/tattoo/works'),
      api.get('/api/tattoo/clients'),
      api.get('/api/tattoo/options'),
    ]
    // Владелец выбирает мастера, мастеру список не нужен
    if (isOwner.value) jobs.push(api.get('/api/admin/masters'))
    const [w, c, o, m] = await Promise.all(jobs)
    works.value = w.works
    clients.value = c
    styleOptions.value = o.styles
    placementOptions.value = o.placements
    complexityOptions.value = o.complexities
    masters.value = m ?? []
  } catch (err) {
    error.value = err.detail ?? err.message
  } finally {
    loading.value = false
  }
}

function openCreate() {
  Object.assign(form, {
    master_id: '',
    client_id: '',
    newClient: false,
    new_name: '',
    new_phone: '',
    new_instagram: '',
    new_note: '',
    size_length_cm: '',
    size_height_cm: '',
    complexity: '',
    style: '',
    placement: '',
    coverup: false,
    session_date: '',
    recommended_price: '',
    cost: '',
    is_final_session: false,
  })
  sketchFiles.value = null
  error.value = ''
  showForm.value = true
}

function openSessionForm(w) {
  sessionWorkId.value = sessionWorkId.value === w.id ? null : w.id
  editWorkId.value = null
  editSession.value = null
  Object.assign(sessionForm, {
    session_date: '',
    recommended_price: '',
    cost: '',
    is_final_session: false,
  })
  resultFiles.value = null
  error.value = ''
}

function openEditWork(w) {
  editWorkId.value = editWorkId.value === w.id ? null : w.id
  sessionWorkId.value = null
  editSession.value = null
  Object.assign(editWorkForm, {
    size_length_cm: w.size_length_cm,
    size_height_cm: w.size_height_cm,
    complexity: w.complexity,
    style: w.style,
    placement: w.placement,
    status: w.status,
  })
  error.value = ''
}

function openEditSession(work, s) {
  // повторный клик по ✏️ того же сеанса — закрыть
  if (editSession.value?.sessionId === s.id) {
    editSession.value = null
    return
  }
  editSession.value = { workId: work.id, sessionId: s.id }
  sessionWorkId.value = null
  editWorkId.value = null
  Object.assign(editSessionForm, {
    session_date: toLocalInput(s.session_date),
    recommended_price: s.recommended_price ?? '',
    cost: s.cost ?? '',
    is_final_session: s.is_final_session,
  })
  error.value = ''
}

/**
 * Слот занят (409): предложить осознанную запись поверх — повторить с force=true.
 * retry(path, body) — повторный запрос; возвращает результат или null (отказ/ошибка уже показаны).
 */
async function forceIfConflict(err, path, body) {
  if (err.status !== 409 || !window.confirm(`${err.detail}\n\nВсё равно записать?`)) {
    error.value = err.detail ?? err.message
    return null
  }
  try {
    return await api.post(`${path}${path.includes('?') ? '&' : '?'}force=true`, body)
  } catch (retryErr) {
    error.value = retryErr.detail ?? retryErr.message
    return null
  }
}

function validateSessionPart(part) {
  if (!part.session_date) return 'Укажите дату и время сеанса'
  if (part.cost && Number(part.cost) <= 0) return 'Стоимость должна быть больше нуля'
  return ''
}

async function submitWork() {
  let clientId = form.client_id ? Number(form.client_id) : null

  if (form.newClient) {
    if (!form.new_name.trim()) {
      error.value = 'Укажите имя клиента'
      return
    }
    if (!form.new_phone.trim() && !form.new_instagram.trim()) {
      error.value = 'Укажите телефон или Instagram — иначе клиента не найти'
      return
    }
    try {
      const createdClient = await api.post('/api/tattoo/clients', {
        full_name: form.new_name.trim(),
        phone: form.new_phone.trim() || null,
        instagram_username: form.new_instagram.trim() || null,
        note: form.new_note.trim() || null,
      })
      clientId = createdClient.id
      // Дедуп: телефон/инста уже были в базе — вернулся существующий клиент
      if (createdClient.full_name !== form.new_name.trim()) {
        notice.value = `Клиент уже был в базе: «${createdClient.full_name}» — работа записана на него.`
      }
    } catch (err) {
      error.value = err.detail ?? err.message
      return
    }
  }

  if (!clientId) {
    error.value = 'Выберите клиента'
    return
  }
  if (isOwner.value && !form.master_id) {
    error.value = 'Выберите мастера'
    return
  }
  const len = Number(form.size_length_cm)
  const hgt = Number(form.size_height_cm)
  if (!len || len <= 0 || !hgt || hgt <= 0) {
    error.value = 'Укажите размер: длину и высоту в см'
    return
  }
  if (!form.complexity || !form.style || !form.placement) {
    error.value = 'Заполните сложность, стиль и место нанесения'
    return
  }
  const sessErr = validateSessionPart(form)
  if (sessErr) {
    error.value = sessErr
    return
  }

  saving.value = true
  error.value = ''
  notice.value = ''
  const workBody = {
    master_id: isOwner.value ? Number(form.master_id) : null,
    client_id: clientId,
    size_length_cm: len,
    size_height_cm: hgt,
    complexity: form.complexity,
    style: form.style,
    placement: form.placement,
    first_session: {
      session_date: toIso(form.session_date),
      recommended_price: form.recommended_price ? Number(form.recommended_price) : null,
      cost: form.cost ? Number(form.cost) : null,
      is_final_session: form.is_final_session,
    },
  }
  try {
    let created
    try {
      created = await api.post('/api/tattoo/works', workBody)
    } catch (err) {
      created = await forceIfConflict(err, '/api/tattoo/works', workBody)
      if (!created) return
    }
    showForm.value = false
    // Фото эскиза — в первый сеанс созданной работы
    let uploaded = 0
    if (sketchFiles.value?.length) {
      const sid = created.sessions[0]?.id
      if (sid) uploaded = await uploadSessionFiles(created.id, sid, 'sketch', sketchFiles.value)
    }
    notice.value =
      'Работа создана, сеанс поставлен в календарь.' +
      (uploaded ? ` Фото эскиза загружено: ${uploaded}.` : '')
    await load()
  } catch (err) {
    error.value = err.detail ?? err.message
  } finally {
    saving.value = false
  }
}

async function submitSession(w) {
  const sessErr = validateSessionPart(sessionForm)
  if (sessErr) {
    error.value = sessErr
    return
  }
  saving.value = true
  error.value = ''
  notice.value = ''
  const path = `/api/tattoo/works/${w.id}/sessions`
  const body = {
    session_date: toIso(sessionForm.session_date),
    recommended_price: sessionForm.recommended_price ? Number(sessionForm.recommended_price) : null,
    cost: sessionForm.cost ? Number(sessionForm.cost) : null,
    is_final_session: sessionForm.is_final_session,
  }
  try {
    let createdSession
    try {
      createdSession = await api.post(path, body)
    } catch (err) {
      createdSession = await forceIfConflict(err, path, body)
      if (!createdSession) return
    }
    sessionWorkId.value = null
    // Фото результата — в только что созданный сеанс
    let uploaded = 0
    if (resultFiles.value?.length) {
      uploaded = await uploadSessionFiles(
        w.id,
        createdSession.id,
        'result',
        resultFiles.value,
      )
    }
    notice.value =
      (sessionForm.is_final_session ? 'Работа завершена.' : 'Сеанс добавлен.') +
      (uploaded ? ` Фото загружено: ${uploaded}.` : '')
    await load()
  } catch (err) {
    error.value = err.detail ?? err.message
  } finally {
    saving.value = false
  }
}

async function submitEditWork(w) {
  const len = Number(editWorkForm.size_length_cm)
  const hgt = Number(editWorkForm.size_height_cm)
  if (!len || len <= 0 || !hgt || hgt <= 0) {
    error.value = 'Укажите размер: длину и высоту в см'
    return
  }
  if (!editWorkForm.complexity || !editWorkForm.style || !editWorkForm.placement) {
    error.value = 'Заполните сложность, стиль и место нанесения'
    return
  }
  saving.value = true
  error.value = ''
  notice.value = ''
  try {
    await api.patch(`/api/tattoo/works/${w.id}`, {
      size_length_cm: len,
      size_height_cm: hgt,
      complexity: editWorkForm.complexity,
      style: editWorkForm.style,
      placement: editWorkForm.placement,
      status: editWorkForm.status,
    })
    editWorkId.value = null
    notice.value = 'Работа обновлена, события в календаре пересинхронизированы.'
    await load()
  } catch (err) {
    error.value = err.detail ?? err.message
  } finally {
    saving.value = false
  }
}

async function submitEditSession(work, s) {
  const err = validateSessionPart(editSessionForm)
  if (err) {
    error.value = err
    return
  }
  saving.value = true
  error.value = ''
  notice.value = ''
  const path = `/api/tattoo/works/${work.id}/sessions/${s.id}`
  const body = {
    session_date: toIso(editSessionForm.session_date),
    recommended_price: editSessionForm.recommended_price
      ? Number(editSessionForm.recommended_price)
      : null,
    cost: editSessionForm.cost ? Number(editSessionForm.cost) : null,
    is_final_session: editSessionForm.is_final_session,
  }
  try {
    await api.patch(path, body)
    editSession.value = null
    notice.value = 'Сеанс обновлён, календарь синхронизирован.'
    await load()
  } catch (err2) {
    // слот занят — предлагаем перезаписать с force
    if (err2.status === 409) {
      const ok = window.confirm(`${err2.detail}\n\nВсё равно перенести?`)
      if (!ok) {
        error.value = err2.detail ?? err2.message
        return
      }
      try {
        await api.patch(`${path}?force=true`, body)
        editSession.value = null
        notice.value = 'Сеанс перенесён поверх занятого времени.'
        await load()
      } catch (err3) {
        error.value = err3.detail ?? err3.message
      }
    } else {
      error.value = err2.detail ?? err2.message
    }
  } finally {
    saving.value = false
  }
}

async function deleteWork(w) {
  error.value = ''
  notice.value = ''
  try {
    await api.del(`/api/tattoo/works/${w.id}`)
    notice.value = 'Работа удалена, события сняты с календаря.'
    await load()
  } catch (err) {
    error.value = err.detail ?? err.message
  }
}

// ---------------------------------------------------------------------------
// Расходники сеанса: списание прямо из «Работы», привязка к сеансу.
// Панель = список позиций склада с полями «сколько использовал».
// ---------------------------------------------------------------------------

const suppliesPanel = ref(null) // { workId, sessionId } | null
const suppliesStock = ref([])
const suppliesLoading = ref(false)
const useAmounts = reactive({}) // supply_id → количество
const useNote = ref('')
const useSaving = ref(false)

function fmtQty(v) {
  return String(v).replace(/(\.\d\d)0+$/, '$1').replace(/(\.\d)0$/, '$1')
}

async function openSuppliesPanel(w, s) {
  // повторный клик по 📦 того же сеанса — закрыть
  if (suppliesPanel.value?.sessionId === s.id) {
    suppliesPanel.value = null
    return
  }
  suppliesPanel.value = { workId: w.id, sessionId: s.id }
  sessionWorkId.value = null
  editWorkId.value = null
  editSession.value = null
  for (const k of Object.keys(useAmounts)) delete useAmounts[k]
  useNote.value = ''
  error.value = ''
  suppliesLoading.value = true
  try {
    suppliesStock.value = (await api.get('/api/supplies')).supplies.filter((x) => x.is_active)
  } catch (err) {
    error.value = err.detail ?? err.message
  } finally {
    suppliesLoading.value = false
  }
}

async function submitSupplies() {
  const rows = suppliesStock.value.filter((s) => Number(useAmounts[s.id]) > 0)
  if (!rows.length) {
    error.value = 'Укажите, что и сколько использовали'
    return
  }
  useSaving.value = true
  error.value = ''
  notice.value = ''
  const failed = []
  let ok = 0
  try {
    for (const s of rows) {
      try {
        await api.post(`/api/supplies/${s.id}/use`, {
          amount: Number(useAmounts[s.id]),
          work_id: suppliesPanel.value.workId,
          session_id: suppliesPanel.value.sessionId,
          note: useNote.value.trim() || null,
        })
        ok += 1
      } catch (err) {
        // не хватило остатка и т.п. — остальное всё равно списываем
        failed.push(`«${s.name}» — ${err.detail ?? err.message}`)
      }
    }
    if (failed.length) {
      error.value = `Не списано: ${failed.join('; ')}`
      await openSuppliesPanel(suppliesPanel.value.workId, { id: suppliesPanel.value.sessionId })
    } else {
      notice.value = `Расходники списаны: позиций — ${ok}.`
      suppliesPanel.value = null
    }
  } finally {
    useSaving.value = false
  }
}

onMounted(load)
</script>

<template>
  <div>
    <div v-if="error" class="error-box">{{ error }}</div>
    <div v-else-if="notice" class="success-box">{{ notice }}</div>

    <div class="toolbar">
      <select v-if="isOwner" v-model="masterFilter" class="filter-select">
        <option value="">Все мастера</option>
        <option v-for="m in masters" :key="m.id" :value="m.id">{{ m.full_name }}</option>
      </select>
      <button class="btn btn-primary btn-sm" @click="openCreate">+ Новая работа</button>
    </div>

    <form v-if="showForm" class="card" style="margin-bottom: 16px" @submit.prevent="submitWork">
      <!-- Блок: клиент -->
      <div class="card inset">
        <div class="section-title">Клиент</div>
        <label class="check client-toggle">
          <input v-model="form.newClient" type="checkbox" />
          Новый клиент (не из Telegram)
        </label>

        <div v-if="form.newClient" class="form-grid">
          <div class="field span-2">
            <label for="w-new-name">Имя</label>
            <input id="w-new-name" v-model="form.new_name" placeholder="Как представился" autocomplete="off" />
          </div>
          <div class="field">
            <label for="w-new-phone">Телефон</label>
            <input id="w-new-phone" v-model="form.new_phone" inputmode="tel" placeholder="+7 701 123-45-67" autocomplete="off" />
          </div>
          <div class="field">
            <label for="w-new-insta">Instagram</label>
            <input id="w-new-insta" v-model="form.new_instagram" placeholder="@username" autocomplete="off" />
          </div>
          <div class="field span-2">
            <label for="w-new-note">Заметка</label>
            <input id="w-new-note" v-model="form.new_note" placeholder="пришла от Аиды, предпочтения…" autocomplete="off" />
          </div>
        </div>
        <div v-else class="form-grid">
          <div class="field span-2">
            <label for="w-client">Клиент из базы</label>
            <select id="w-client" v-model="form.client_id">
              <option value="" disabled>Выберите клиента</option>
              <option v-for="c in clients" :key="c.id" :value="c.id">
                {{ c.full_name }}<template v-if="c.phone"> — {{ c.phone }}</template>
              </option>
            </select>
          </div>
        </div>
      </div>

      <!-- Блок: работа -->
      <div class="card inset">
        <div class="section-title">Работа</div>
        <!-- Сетка 3×2 построчно: мастер | длина | стиль / сложность | высота | место -->
        <div class="work-grid">
          <div v-if="isOwner" class="field">
            <label for="w-master">Мастер</label>
            <select id="w-master" v-model="form.master_id">
              <option value="" disabled>Выберите мастера</option>
              <option v-for="m in activeMasters" :key="m.id" :value="m.id">{{ m.full_name }}</option>
            </select>
          </div>
          <div class="field">
            <label for="w-size-l">Длина, см</label>
            <input id="w-size-l" v-model="form.size_length_cm" inputmode="decimal" placeholder="например 15" autocomplete="off" />
          </div>
          <div class="field">
            <label for="w-style">Стиль</label>
            <select id="w-style" v-model="form.style">
              <option value="" disabled>Выберите стиль</option>
              <option v-for="s in styleOptions" :key="s" :value="s">{{ s }}</option>
            </select>
          </div>
          <div class="field">
            <label for="w-complexity">Сложность</label>
            <select id="w-complexity" v-model="form.complexity">
              <option value="" disabled>Выберите сложность</option>
              <option v-for="c in complexityOptions" :key="c" :value="c">{{ c }}</option>
            </select>
          </div>
          <div class="field">
            <label for="w-size-h">Высота, см</label>
            <input id="w-size-h" v-model="form.size_height_cm" inputmode="decimal" placeholder="например 10" autocomplete="off" />
          </div>
          <div class="field">
            <label for="w-placement">Место нанесения</label>
            <select id="w-placement" v-model="form.placement">
              <option value="" disabled>Выберите зону</option>
              <option v-for="p in placementOptions" :key="p.value" :value="p.value">
                {{ p.value }}{{ p.difficult ? ' ⚠️' : '' }}
              </option>
            </select>
            <div v-if="isDifficultPlacement" class="muted" style="font-size: 12px; margin-top: 4px">
              ⚠️ Сложная зона: тонкая кожа / трение / болезненность
            </div>
          </div>
          <label class="check span-2">
            <input v-model="form.coverup" type="checkbox" />
            Перекрытие (cover-up) / работа по шраму
          </label>
        </div>
      </div>

      <!-- Блок: первый сеанс -->
      <div class="card inset">
        <div class="section-title">Первый сеанс</div>
        <div class="form-grid">
          <div class="field">
            <label for="w-date">Дата и время</label>
            <input id="w-date" v-model="form.session_date" type="datetime-local" />
          </div>
          <div class="field">
            <label for="w-quoted">Рекомендуемая цена, ₸</label>
            <input id="w-quoted" v-model="form.recommended_price" inputmode="decimal" placeholder="рассчитается по прайсу" autocomplete="off" />
          </div>
          <div class="field">
            <label for="w-cost">Договорились на, ₸</label>
            <input id="w-cost" v-model="form.cost" inputmode="decimal" placeholder="за сколько договорились" autocomplete="off" />
          </div>
        </div>
        <label class="check">
          <input v-model="form.is_final_session" type="checkbox" />
          Этим сеансом работа закрывается
        </label>
      </div>

      <!-- Блок: фото эскиза -->
      <div class="card inset">
        <div class="section-title">Фото эскиза</div>
        <div class="field">
          <input
            id="w-sketch"
            type="file"
            accept="image/*"
            multiple
            @change="(e) => (sketchFiles = e.target.files)"
          />
          <div v-if="sketchCount" class="muted" style="font-size: 12px; margin-top: 4px">
            Выбрано: {{ sketchCount }}
          </div>
        </div>
      </div>

      <div style="display: flex; gap: 10px">
        <button class="btn btn-primary" type="submit" :disabled="saving">
          {{ saving ? 'Сохраняем…' : 'Создать' }}
        </button>
        <button class="btn btn-ghost" type="button" @click="showForm = false">Отмена</button>
      </div>
    </form>

    <div v-if="loading" class="muted">Загрузка…</div>

    <div v-else-if="!works.length" class="card">
      <div class="empty-state">
        <div class="empty-title">Работ пока нет</div>
        <div v-if="isOwner && !clients.length">
          Клиенты появляются в чатах — напишите им или дождитесь первого сообщения
        </div>
        <div v-else>Создайте первую работу — сеанс сразу попадёт в Google Calendar</div>
        <button class="btn btn-primary btn-sm" @click="openCreate">+ Новая работа</button>
      </div>
    </div>

    <div v-else class="table-wrap">
      <table class="table">
        <thead>
          <tr>
            <th>Клиент</th>
            <th>Параметры</th>
            <th>Сеансы</th>
            <th>Итого</th>
            <th>Статус</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          <template v-for="w in works" :key="w.id">
            <tr v-if="!isOwner || !masterFilter || Number(masterFilter) === w.master_id">
              <td>
                <div>{{ clientName(w.client_id) }}</div>
                <div v-if="isOwner" class="muted" style="font-size: 12px">{{ masterName(w.master_id) }}</div>
              </td>
              <td>
                <div>{{ w.size_length_cm }}×{{ w.size_height_cm }} см · {{ w.complexity }}</div>
                <div class="muted" style="font-size: 12px">{{ w.style }}</div>
                <div class="muted" style="font-size: 12px">{{ w.placement }}</div>
              </td>
              <td>
                <div v-for="s in w.sessions" :key="s.id" class="session-row">
                  <span>{{ fmtDate(s.session_date) }}</span>
                  <span class="muted">
                    — {{ s.cost != null ? money(s.cost) : (s.recommended_price != null ? `по прайсу ${money(s.recommended_price)}` : 'факт не указан') }}
                  </span>
                  <span v-if="s.is_final_session" class="badge badge-green">финал</span>
                  <button class="btn btn-sm" type="button" @click="openEditSession(w, s)">✏️</button>
                  <button class="btn btn-sm" type="button" @click="openPhotoPicker(w, s)">📷</button>
                  <button
                    class="btn btn-sm"
                    type="button"
                    title="Расходники сеанса"
                    @click="openSuppliesPanel(w, s)"
                  >📦</button>
                </div>
                <div v-if="!w.sessions.length" class="muted">—</div>
                <template v-for="s in w.sessions" :key="`f-${s.id}`">
                  <div v-if="s.files.length" class="thumbs">
                    <span v-for="f in s.files" :key="f.id" class="thumb">
                      <a :href="f.file_url" target="_blank" rel="noopener">
                        <img :src="f.file_url" :alt="f.original_name" loading="lazy" />
                      </a>
                      <button class="thumb-del" type="button" title="Удалить фото" @click="deletePhoto(f)">✕</button>
                    </span>
                  </div>
                </template>
              </td>
              <td style="white-space: nowrap">{{ money(workTotal(w)) }}</td>
              <td>
                <span class="badge" :class="w.status === 'completed' ? 'badge-green' : 'badge-muted'">
                  {{ STATUS_LABELS[w.status] ?? w.status }}
                </span>
              </td>
              <td style="text-align: right; white-space: nowrap">
                <button class="btn btn-sm" @click="openEditWork(w)">✏️ Править</button>
                <button class="btn btn-sm" @click="openSessionForm(w)">+ Сеанс</button>
                <button class="btn btn-sm btn-ghost" @click="deleteWork(w)">Удалить</button>
              </td>
            </tr>

            <!-- Редактирование работы -->
            <tr v-if="editWorkId === w.id">
              <td colspan="6" style="background: rgba(108, 99, 255, 0.05)">
                <form class="card inset" style="margin-top: 0" @submit.prevent="submitEditWork(w)">
                  <div class="section-title">Правка работы — {{ clientName(w.client_id) }}</div>
                  <div class="work-grid">
                    <div class="field">
                      <label :for="`e-size-l-${w.id}`">Длина, см</label>
                      <input :id="`e-size-l-${w.id}`" v-model="editWorkForm.size_length_cm" inputmode="decimal" autocomplete="off" />
                    </div>
                    <div class="field">
                      <label :for="`e-style-${w.id}`">Стиль</label>
                      <select :id="`e-style-${w.id}`" v-model="editWorkForm.style">
                        <option v-for="s in styleOptions" :key="s" :value="s">{{ s }}</option>
                      </select>
                    </div>
                    <div class="field">
                      <label :for="`e-complexity-${w.id}`">Сложность</label>
                      <select :id="`e-complexity-${w.id}`" v-model="editWorkForm.complexity">
                        <option v-for="c in complexityOptions" :key="c" :value="c">{{ c }}</option>
                      </select>
                    </div>
                    <div class="field">
                      <label :for="`e-size-h-${w.id}`">Высота, см</label>
                      <input :id="`e-size-h-${w.id}`" v-model="editWorkForm.size_height_cm" inputmode="decimal" autocomplete="off" />
                    </div>
                    <div class="field">
                      <label :for="`e-placement-${w.id}`">Место нанесения</label>
                      <select :id="`e-placement-${w.id}`" v-model="editWorkForm.placement">
                        <option v-for="p in placementOptions" :key="p.value" :value="p.value">
                          {{ p.value }}{{ p.difficult ? ' ⚠️' : '' }}
                        </option>
                      </select>
                    </div>
                    <div class="field">
                      <label :for="`e-status-${w.id}`">Статус</label>
                      <select :id="`e-status-${w.id}`" v-model="editWorkForm.status">
                        <option v-for="(label, key) in STATUS_LABELS" :key="key" :value="key">{{ label }}</option>
                      </select>
                    </div>
                  </div>
                  <p class="muted" style="font-size: 12px; margin: 8px 0 0">
                    Клиент и мастер не меняются — для этого отредактируйте клиента в разделе «Клиенты».
                  </p>
                  <div style="display: flex; gap: 10px; margin-top: 12px">
                    <button class="btn btn-primary" type="submit" :disabled="saving">Сохранить</button>
                    <button class="btn btn-ghost" type="button" @click="editWorkId = null">Отмена</button>
                  </div>
                </form>
              </td>
            </tr>

            <!-- Редактирование сеанса -->
            <tr v-if="editSession?.workId === w.id">
              <td colspan="6" style="background: rgba(108, 99, 255, 0.05)">
                <form class="card inset" style="margin-top: 0" @submit.prevent="submitEditSession(w, w.sessions.find((x) => x.id === editSession.sessionId))">
                  <div class="section-title">Правка сеанса</div>
                  <div class="form-grid">
                    <div class="field">
                      <label :for="`es-date-${w.id}`">Дата и время</label>
                      <input :id="`es-date-${w.id}`" v-model="editSessionForm.session_date" type="datetime-local" />
                    </div>
                    <div class="field">
                      <label :for="`es-quoted-${w.id}`">Рекомендуемая цена, ₸</label>
                      <input :id="`es-quoted-${w.id}`" v-model="editSessionForm.recommended_price" inputmode="decimal" placeholder="пусто — убрать" autocomplete="off" />
                    </div>
                    <div class="field">
                      <label :for="`es-cost-${w.id}`">Договорились на, ₸</label>
                      <input :id="`es-cost-${w.id}`" v-model="editSessionForm.cost" inputmode="decimal" placeholder="за сколько договорились" autocomplete="off" />
                    </div>
                  </div>
                  <label class="check">
                    <input v-model="editSessionForm.is_final_session" type="checkbox" />
                    Этим сеансом работа закрывается
                  </label>
                  <div style="display: flex; gap: 10px">
                    <button class="btn btn-primary" type="submit" :disabled="saving">Сохранить</button>
                    <button class="btn btn-ghost" type="button" @click="editSession = null">Отмена</button>
                  </div>
                </form>
              </td>
            </tr>

            <!-- Расходники сеанса: списание со склада с привязкой к сеансу -->
            <tr v-if="suppliesPanel?.workId === w.id">
              <td colspan="6" style="background: rgba(108, 99, 255, 0.05)">
                <form class="card inset" style="margin-top: 0" @submit.prevent="submitSupplies">
                  <div class="section-title">Расходники — сеанс {{ fmtDate(w.sessions.find((x) => x.id === suppliesPanel.sessionId)?.session_date) }}</div>
                  <div v-if="suppliesLoading" class="muted">Загрузка склада…</div>
                  <div v-else-if="!suppliesStock.length" class="muted">
                    Склад пуст — позиции добавляет владелец в разделе «Склад».
                  </div>
                  <template v-else>
                    <div class="supplies-list">
                      <div v-for="s in suppliesStock" :key="s.id" class="supply-line" :class="{ 'supply-low': s.low }">
                        <span class="supply-name">
                          {{ s.name }}
                          <span class="muted">· {{ fmtQty(s.quantity) }} {{ s.unit }}</span>
                          <span v-if="s.low" class="badge badge-red">кончается</span>
                        </span>
                        <input
                          v-model="useAmounts[s.id]"
                          class="supply-amount"
                          type="number"
                          min="0"
                          :max="s.quantity"
                          step="0.01"
                          inputmode="decimal"
                          :placeholder="`0 ${s.unit}`"
                          autocomplete="off"
                        />
                      </div>
                    </div>
                    <div class="field" style="margin-top: 10px">
                      <label :for="`sup-note-${w.id}`">Комментарий</label>
                      <input :id="`sup-note-${w.id}`" v-model="useNote" placeholder="необязательно" autocomplete="off" />
                    </div>
                    <div style="display: flex; gap: 10px">
                      <button class="btn btn-primary" type="submit" :disabled="useSaving">
                        {{ useSaving ? 'Списываем…' : 'Списать' }}
                      </button>
                      <button class="btn btn-ghost" type="button" @click="suppliesPanel = null">Отмена</button>
                    </div>
                  </template>
                </form>
              </td>
            </tr>

            <tr v-if="sessionWorkId === w.id">
              <td colspan="6" style="background: rgba(108, 99, 255, 0.05)">
                <form class="card inset" style="margin-top: 0" @submit.prevent="submitSession(w)">
                  <div class="form-grid">
                    <div class="field">
                      <label :for="`s-date-${w.id}`">Дата и время</label>
                      <input :id="`s-date-${w.id}`" v-model="sessionForm.session_date" type="datetime-local" />
                    </div>
                    <div class="field">
                      <label :for="`s-quoted-${w.id}`">Рекомендуемая цена, ₸</label>
                      <input :id="`s-quoted-${w.id}`" v-model="sessionForm.recommended_price" inputmode="decimal" placeholder="необязательно" autocomplete="off" />
                    </div>
                    <div class="field">
                      <label :for="`s-cost-${w.id}`">Договорились на, ₸</label>
                      <input :id="`s-cost-${w.id}`" v-model="sessionForm.cost" inputmode="decimal" placeholder="за сколько договорились" autocomplete="off" />
                    </div>
                  </div>
                  <label class="check">
                    <input v-model="sessionForm.is_final_session" type="checkbox" />
                    Этим сеансом работа закрывается
                  </label>
                  <div class="field">
                    <label :for="`s-photos-${w.id}`">Фото результата</label>
                    <input
                      :id="`s-photos-${w.id}`"
                      type="file"
                      accept="image/*"
                      multiple
                      @change="(e) => (resultFiles = e.target.files)"
                    />
                    <div v-if="resultCount" class="muted" style="font-size: 12px">
                      Выбрано: {{ resultCount }}
                    </div>
                  </div>
                  <div style="display: flex; gap: 10px">
                    <button class="btn btn-primary" type="submit" :disabled="saving">Добавить</button>
                    <button class="btn btn-ghost" type="button" @click="sessionWorkId = null">Отмена</button>
                  </div>
                </form>
              </td>
            </tr>
          </template>
        </tbody>
      </table>
    </div>

    <!-- Скрытый пикер «+ фото» у сеанса; таргет в photoTarget -->
    <input
      id="session-photo-input"
      type="file"
      accept="image/*"
      multiple
      style="display: none"
      @change="onSessionPhotosPicked"
    />
  </div>
</template>

<style scoped>
.filter-select {
  max-width: 220px;
}
/* Форма создания работы разбита на блоки: клиент / работа / сеанс / фото */
form .card.inset {
  padding: 12px 14px;
}
.section-title {
  font-size: 11px;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.08em;
  color: var(--muted);
  margin-bottom: 10px;
}
.span-2 {
  grid-column: 1 / -1;
}
/* Сетка блока «Работа»: 3 колонки × 2 ряда, построчно */
.work-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 0 16px;
}
.card.inset {
  background: rgba(108, 99, 255, 0.04);
  margin: 12px 0;
  padding: 12px;
}
.session-row {
  display: flex;
  gap: 8px;
  align-items: center;
  font-size: 13px;
}
.thumbs {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-top: 6px;
}
.thumb {
  position: relative;
  display: inline-block;
}
.thumb img {
  width: 56px;
  height: 56px;
  object-fit: cover;
  border-radius: 8px;
  display: block;
}
.thumb-del {
  position: absolute;
  top: -6px;
  right: -6px;
  width: 18px;
  height: 18px;
  border-radius: 50%;
  border: none;
  background: #333;
  color: #fff;
  font-size: 10px;
  line-height: 1;
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
}
.check {
  display: flex;
  gap: 8px;
  align-items: center;
  margin: 10px 0 14px;
  font-size: 14px;
}
.client-toggle {
  margin: 0 0 6px;
  font-weight: 500;
}
/* Панель расходников сеанса */
.supplies-list {
  display: flex;
  flex-direction: column;
  gap: 4px;
  max-height: 320px;
  overflow-y: auto;
}
.supply-line {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 4px 0;
  font-size: 13.5px;
}
.supply-line.supply-low {
  background: rgba(220, 60, 60, 0.06);
  border-radius: 6px;
  padding: 4px 6px;
}
.supply-name {
  min-width: 0;
}
.supply-amount {
  width: 90px;
  flex-shrink: 0;
  text-align: right;
}
</style>