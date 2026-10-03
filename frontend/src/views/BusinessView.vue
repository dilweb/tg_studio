<script setup>
import { computed, onMounted, reactive, ref } from 'vue'

import { api } from '../api/client'

const info = ref(null)
const loading = ref(true)
const error = ref('')

// ── Прайс: глобальный % и все коэффициенты ───────────────────────────────────
const pricing = reactive({
  global_percent: 100,
  size_rates: { xs: 0, s: 0, m: 0, l: 0 },
  style_factors: {},
  zone_factors: { std: 1, elevated: 1.2, critical: 1.4 },
  coverup_factor: 1.4,
})
const pricingBusy = ref(false)
const pricingSaved = ref(false)
const defaultStyles = [
  'Минимализм (Minimalism)',
  'Лайнворк (Linework)',
  'Леттеринг (Lettering)',
  'Хэндпоук (Handpoke)',
  'Традиционный (Traditional / Old School)',
  'Нью-скул (New School)',
  'Геометрия (Geometry)',
  'Акварель (Watercolor)',
  'Орнаментал (Ornamental)',
  'Дотворк (Dotwork)',
  'Трайбл (Tribal)',
  'Нео-традишнл (Neo-Traditional)',
  'Чикано (Chicano)',
  'Эскизный / Скетч стайл (Sketch style)',
  'Реализм (Realism)',
  'Блэкворк (Blackwork)',
  'Графика / Гравюра (Engraving / Woodcut)',
  'Японский (Irezumi)',
  'Киберпанк / Трэш-полька (Cyberpunk / Trash Polka)',
  'Биомеханика / Биоорганика (Biomechanics / Bioorganic)',
]
// Короткое имя стиля для подписи («Минимализм» вместо полного значения справочника)
const shortStyle = (s) => s.split(' (')[0]

async function loadPricing() {
  if (!info.value) return
  const cfg = info.value.pricing_config
  if (!cfg) return
  pricing.global_percent = cfg.global_percent ?? 100
  Object.assign(pricing.size_rates, cfg.size_rates ?? {})
  pricing.style_factors = { ...(cfg.style_factors ?? {}) }
  Object.assign(pricing.zone_factors, cfg.zone_factors ?? {})
  pricing.coverup_factor = cfg.coverup_factor ?? 1.4
}

async function savePricing() {
  pricingBusy.value = true
  pricingSaved.value = false
  error.value = ''
  try {
    const num = (v) => (v === '' || v == null ? 0 : Number(v))
    info.value = await api.put('/api/admin/business/pricing', {
      global_percent: Number(pricing.global_percent) || 100,
      size_rates: Object.fromEntries(
        Object.entries(pricing.size_rates).map(([k, v]) => [k, num(v)]),
      ),
      style_factors: Object.fromEntries(
        Object.entries(pricing.style_factors).map(([k, v]) => [k, Number(v) || 1]),
      ),
      zone_factors: Object.fromEntries(
        Object.entries(pricing.zone_factors).map(([k, v]) => [k, Number(v) || 1]),
      ),
      coverup_factor: Number(pricing.coverup_factor) || 1.4,
    })
    await loadPricing()
    pricingSaved.value = true
    setTimeout(() => (pricingSaved.value = false), 3000)
  } catch (err) {
    error.value = err.detail ?? err.message
  } finally {
    pricingBusy.value = false
  }
}

const gcalStatus = ref(null) // { connected, email, share_email }
const gcalLoading = ref(true)
const gcalBusy = ref(false)

const shareEmail = ref('')
const shareBusy = ref(false)
const shareResult = ref(null)

const shareMessage = computed(() => {
  if (!shareResult.value) return ''
  const ok = shareResult.value.shared.filter((c) => c.ok).length
  const total = shareResult.value.shared.length
  const failed = shareResult.value.shared.filter((c) => !c.ok)
  const base = `Доступ открыт для ${ok} из ${total} календарей. Письмо от Google придёт на ${shareResult.value.email}.`
  if (ok === total) return base
  return `${base} Ошибки: ${failed.map((f) => `${f.calendar_id} — ${f.error}`).join('; ')}`
})

async function loadGcalStatus() {
  gcalLoading.value = true
  try {
    gcalStatus.value = await api.get('/api/admin/google-calendar/status')
    if (gcalStatus.value?.share_email) shareEmail.value = gcalStatus.value.share_email
  } catch (err) {
    error.value = err.detail ?? err.message
  } finally {
    gcalLoading.value = false
  }
}

async function shareGcal() {
  error.value = ''
  shareBusy.value = true
  shareResult.value = null
  try {
    shareResult.value = await api.post('/api/admin/google-calendar/share', {
      email: shareEmail.value.trim(),
    })
  } catch (err) {
    error.value = err.detail ?? err.message
  } finally {
    shareBusy.value = false
  }
}

async function connectGcal() {
  error.value = ''
  gcalBusy.value = true
  try {
    await api.post('/api/admin/google-calendar/connect')
    await loadGcalStatus()
  } catch (err) {
    error.value = err.detail ?? err.message
  } finally {
    gcalBusy.value = false
  }
}

async function disconnectGcal() {
  error.value = ''
  gcalBusy.value = true
  try {
    await api.del('/api/admin/google-calendar/disconnect')
    await loadGcalStatus()
  } catch (err) {
    error.value = err.detail ?? err.message
  } finally {
    gcalBusy.value = false
  }
}

onMounted(async () => {
  try {
    info.value = await api.get('/api/admin/business')
  } catch (err) {
    error.value = err.detail ?? err.message
  } finally {
    loading.value = false
  }
  await loadPricing()
  await loadGcalStatus()
})
</script>

<template>
  <div>
    <div v-if="error" class="error-box">{{ error }}</div>

    <div v-if="loading" class="muted">Загрузка…</div>

    <template v-else-if="info">
      <div class="card" style="margin-bottom: 16px">
        <div class="section-head">
          <h2>{{ info.name }}</h2>
          <span class="badge" :class="info.is_active ? 'badge-green' : 'badge-muted'">
            {{ info.is_active ? 'Активен' : 'Выключен' }}
          </span>
        </div>
        <div v-if="info.description" style="margin-bottom: 12px">{{ info.description }}</div>
        <div style="display: flex; gap: 24px; flex-wrap: wrap; font-size: 13px">
          <div>
            <div class="muted">Телефон</div>
            <div>{{ info.phone ?? '—' }}</div>
          </div>
          <div>
            <div class="muted">Telegram владельца</div>
            <div>{{ info.owner_telegram_id }}</div>
          </div>
        </div>
      </div>

      <div class="card">
        <div class="section-head">
          <h2>Прайс и коэффициенты</h2>
          <span v-if="pricingSaved" class="badge badge-green">Сохранено</span>
        </div>
        <p class="muted" style="font-size: 13px; margin-bottom: 16px">
          Рекомендуемая цена в форме записи считается по формуле:
          ставка размера × стиль × зона × доп., затем — глобальный процент.
          <strong>Глобальный процент 100 — базовый прайс</strong>; ниже — скидки/акции,
          выше — наценка.
        </p>

        <div style="display: flex; gap: 16px; flex-wrap: wrap; margin-bottom: 16px">
          <div>
            <label class="muted" style="display: block; font-size: 13px; margin-bottom: 4px">
              Глобальный процент, %
            </label>
            <input v-model="pricing.global_percent" type="number" min="10" max="500" step="1" style="max-width: 120px" />
          </div>
          <div>
            <label class="muted" style="display: block; font-size: 13px; margin-bottom: 4px">
              Cover-up / шрам (×)
            </label>
            <input v-model="pricing.coverup_factor" type="number" min="0.1" max="10" step="0.05" style="max-width: 120px" />
          </div>
        </div>

        <div class="muted" style="font-size: 13px; margin-bottom: 6px">Ставки размера, ₸ (по длинной стороне)</div>
        <div style="display: flex; gap: 12px; flex-wrap: wrap; margin-bottom: 16px">
          <div v-for="(label, key) in { xs: 'XS ≤ 5', s: 'S ≤ 10', m: 'M ≤ 20', l: 'L > 20' }" :key="key">
            <label class="muted" style="display: block; font-size: 12px; margin-bottom: 4px">{{ label }} см</label>
            <input v-model="pricing.size_rates[key]" type="number" min="0" style="max-width: 130px" />
          </div>
        </div>

        <div class="muted" style="font-size: 13px; margin-bottom: 6px">Коэффициенты зон (×)</div>
        <div style="display: flex; gap: 12px; flex-wrap: wrap; margin-bottom: 16px">
          <div v-for="(label, key) in { std: 'Стандарт', elevated: 'Повышенная', critical: 'Критическая' }" :key="key">
            <label class="muted" style="display: block; font-size: 12px; margin-bottom: 4px">{{ label }}</label>
            <input v-model="pricing.zone_factors[key]" type="number" min="0.1" max="10" step="0.05" style="max-width: 110px" />
          </div>
        </div>

        <div class="muted" style="font-size: 13px; margin-bottom: 6px">Коэффициенты стилей (×)</div>
        <div class="styles-grid">
          <div v-for="s in defaultStyles" :key="s">
            <label class="muted" style="display: block; font-size: 12px; margin-bottom: 4px">{{ shortStyle(s) }}</label>
            <input v-model="pricing.style_factors[s]" type="number" min="0.1" max="10" step="0.05" />
          </div>
        </div>

        <div style="margin-top: 16px">
          <button class="btn btn-primary" :disabled="pricingBusy" @click="savePricing">
            {{ pricingBusy ? 'Сохраняем…' : 'Сохранить прайс' }}
          </button>
        </div>
      </div>

      <div class="card">
        <div class="section-head">
          <h2>Google Calendar</h2>
          <span
            v-if="!gcalLoading"
            class="badge"
            :class="gcalStatus?.connected ? 'badge-green' : 'badge-yellow'"
          >
            {{ gcalStatus?.connected ? 'Подключен' : 'Не подключен' }}
          </span>
        </div>
        <p class="muted" style="font-size: 13px; margin-bottom: 16px">
          Сеансы мастеров синхронизируются с этим Google-аккаунтом, а свободные слоты
          вычисляются с учётом занятости в нём. У каждого мастера может быть свой
          отдельный календарь внутри этого аккаунта — настраивается в разделе «Мастера».
        </p>

        <div v-if="gcalLoading" class="muted">Загрузка…</div>
        <template v-else-if="gcalStatus?.connected">
          <div style="margin-bottom: 12px">
            Аккаунт: <strong>{{ gcalStatus.email }}</strong>
          </div>

          <div style="margin-bottom: 16px">
            <label
              class="muted"
              for="gcal-share-email"
              style="display: block; font-size: 13px; margin-bottom: 6px"
            >
              Ваш Google-адрес — записи появятся в вашем Google Calendar
            </label>
            <div style="display: flex; gap: 10px; align-items: center; flex-wrap: wrap">
              <input
                id="gcal-share-email"
                v-model="shareEmail"
                type="email"
                autocomplete="email"
                placeholder="you@gmail.com"
                style="max-width: 280px"
              />
              <button class="btn btn-primary" :disabled="shareBusy" @click="shareGcal">
                {{ shareBusy ? 'Открываем доступ…' : 'Открыть доступ' }}
              </button>
            </div>
            <p v-if="shareMessage" class="muted" style="font-size: 12px; margin-top: 8px">
              {{ shareMessage }}
              Если календарь не появился сам: calendar.google.com → «+» рядом с «Другие
              календари» → «Подписка на календарь» → адрес {{ gcalStatus.email }}.
            </p>
          </div>

          <button class="btn btn-ghost" :disabled="gcalBusy" @click="disconnectGcal">
            Отключить
          </button>
        </template>
        <template v-else>
          <div style="display: flex; gap: 10px; align-items: center">
            <button class="btn btn-primary" :disabled="gcalBusy" @click="connectGcal">
              Подключить Google Calendar
            </button>
            <button class="btn btn-sm btn-ghost" :disabled="gcalBusy" @click="loadGcalStatus">
              Обновить статус
            </button>
          </div>
          <p class="muted" style="font-size: 12px; margin-top: 8px">
            Подключение через сервисный аккаунт студии — без окна Google. После
            подключения создайте мастерам календари в разделе «Мастера».
          </p>
        </template>
      </div>
    </template>

    <div v-else class="card">
      <div class="empty-state">
        <div class="empty-title">Бизнес не найден</div>
        <div>Похоже, профиль бизнеса ещё не создан</div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.styles-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(160px, 1fr));
  gap: 10px 12px;
}
.styles-grid input {
  width: 100%;
  max-width: 130px;
}
</style>
