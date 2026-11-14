// Мост к Telegram WebApp: тема, шапка, BackButton, внешние ссылки.
// Всё опционально: вне Telegram (браузер, dev) функции безопасно no-op —
// CSS-переменные остаются с дефолтами из main.css (тёмная тема).

function tg() {
  return window.Telegram?.WebApp ?? null
}

// Фолбэки, если клиент не отдал параметр темы: подобраны под светлую/тёмную
// палитры Telegram, чтобы приложение не «рвалось» на границе с шапкой.
const FALLBACK = {
  dark: {
    bg: '#0f1117',
    surface: '#1a1d27',
    surface2: '#222536',
    border: '#2e3149',
    text: '#e2e8f0',
    muted: '#64748b',
    red: '#ef4444',
  },
  light: {
    bg: '#ffffff',
    surface: '#f5f6fa',
    surface2: '#eceef4',
    border: '#dde0ea',
    text: '#1c2333',
    muted: '#70768a',
    red: '#e5484d',
  },
}

/**
 * Маппинг themeParams на CSS-переменные main.css.
 * Брендовый --accent не перезаписываем: это фирменный цвет, не кнопка Telegram.
 */
export function applyTelegramTheme() {
  const t = tg()
  const params = t?.themeParams
  if (!params) return

  const fb = FALLBACK[t.colorScheme === 'light' ? 'light' : 'dark']
  const root = document.documentElement
  const set = (name, value) => root.style.setProperty(name, value)

  set('--bg', params.bg_color ?? fb.bg)
  set('--surface', params.section_bg_color ?? params.secondary_bg_color ?? fb.surface)
  set('--surface2', params.secondary_bg_color ?? params.section_bg_color ?? fb.surface2)
  set('--border', params.separator_color ?? fb.border)
  set('--text', params.text_color ?? fb.text)
  set('--muted', params.hint_color ?? params.subtitle_text_color ?? fb.muted)
  set('--red', params.destructive_text_color ?? fb.red)
  root.style.colorScheme = t.colorScheme === 'light' ? 'light' : 'dark'

  const bg = params.bg_color ?? fb.bg
  try {
    t.setHeaderColor(bg)
    t.setBackgroundColor(bg)
  } catch {
    // старые клиенты могут не уметь setHeaderColor
  }
}

/** Применить тему сейчас и дальше следить за themeChanged. */
export function watchTelegramTheme() {
  applyTelegramTheme()
  tg()?.onEvent?.('themeChanged', applyTelegramTheme)
}

/**
 * Внешняя ссылка (Kaspi-оплата и т.п.): window.open в мобильном WebView
 * блокируется как popup — внутри Telegram открываем через openLink.
 */
export function openLink(url) {
  const t = tg()
  if (t?.openLink) {
    t.openLink(url)
  } else {
    window.open(url, '_blank', 'noopener')
  }
}

/** Показать Telegram-кнопку «Назад» (например, для открытого меню). */
export function showBackButton(onClick) {
  const bb = tg()?.BackButton
  if (!bb) return
  bb.onClick(onClick)
  bb.show()
}

/** Скрыть кнопку «Назад», сняв обработчик. */
export function hideBackButton(onClick) {
  const bb = tg()?.BackButton
  if (!bb) return
  if (onClick) bb.offClick(onClick)
  bb.hide()
}
