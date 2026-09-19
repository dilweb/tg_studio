// Ошибка текстом на экране вместо чёрного экрана.
// Оверлей — чистый DOM (без Vue и без CSS-сборки): показывает ошибки,
// даже если приложение не смонтировалось или стили не загрузились.

const OVERLAY_ID = 'fatal-error-overlay'
const MAX_BLOCKS = 20

function formatError(err) {
  if (err instanceof Error) {
    return `${err.name}: ${err.message}\n\n${err.stack ?? ''}`
  }
  if (typeof err === 'object' && err !== null) {
    try {
      return JSON.stringify(err, null, 2)
    } catch {
      return String(err)
    }
  }
  return String(err)
}

/** Показать ошибку поверх страницы (можно вызывать многократно — блоки копятся). */
export function showFatalError(title, err) {
  if (typeof document === 'undefined') return
  console.error(`[frontend] ${title}:`, err)

  let overlay = document.getElementById(OVERLAY_ID)
  if (!overlay) {
    overlay = document.createElement('div')
    overlay.id = OVERLAY_ID
    overlay.style.cssText =
      'position:fixed;inset:0;z-index:99999;overflow:auto;padding:20px;box-sizing:border-box;' +
      'background:#160708;color:#ffd9d9;font:13px/1.5 ui-monospace,SFMono-Regular,Menlo,monospace;'

    const close = document.createElement('button')
    close.type = 'button'
    close.textContent = '✕ Скрыть'
    close.style.cssText =
      'position:fixed;top:12px;right:12px;padding:6px 12px;border-radius:8px;border:1px solid #7a2b2b;' +
      'background:#2a0d0d;color:#ffd9d9;font:inherit;cursor:pointer;'
    close.addEventListener('click', () => overlay.remove())

    const log = document.createElement('div')
    log.style.cssText = 'max-width:760px;margin:0 auto;'
    overlay.append(close, log)
    document.body.append(overlay)
  }

  const block = document.createElement('pre')
  block.style.cssText = 'white-space:pre-wrap;word-break:break-word;margin:0 0 14px;padding:12px;' +
    'border:1px solid #7a2b2b;border-radius:10px;background:#220909;'
  block.textContent = `⛔ ${title}\n\n${formatError(err)}`

  const log = overlay.lastChild
  log.append(block)
  // при потоке многократных ошибок не даём экрану разрастись бесконечно
  while (log.children.length > MAX_BLOCKS) log.firstChild.remove()
  overlay.scrollTop = overlay.scrollHeight
}
