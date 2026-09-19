import { createApp } from 'vue'

import App from './App.vue'
import router from './router'
import { showFatalError } from './errors'
import './styles/main.css'

// Любая непойманная ошибка — текстом на экране, а не чёрным фоном.
// window.error с capture=true ловит и падения скриптов, и незагрузившиеся ресурсы.
window.addEventListener(
  'error',
  (event) => {
    if (event.error) {
      showFatalError('Ошибка приложения', event.error)
    } else if (event.target && event.target !== window) {
      const url = event.target.src || event.target.href
      showFatalError('Не загрузился ресурс', url || event.target.tagName)
    }
  },
  true,
)
window.addEventListener('unhandledrejection', (event) => {
  showFatalError('Необработанная ошибка (promise)', event.reason)
})

const tg = window.Telegram?.WebApp
if (tg) {
  tg.ready()
  tg.expand()
}

const app = createApp(App)
// Ошибки рендера/lifecycle Vue по умолчанию глотаются — показываем и их
app.config.errorHandler = (err, _instance, info) => {
  showFatalError(`Ошибка Vue (${info})`, err)
}
app.use(router)
// Провал lazy-импорта чанка или падение guard'а роутера
router.onError((err) => showFatalError('Ошибка навигации', err))

// Redirect-цикл не бросает ошибок — навигация просто крутится на чёрном экране.
// Ловим аномальную частоту переходов и показываем маршрут-цепочку.
let navTimes = []
router.afterEach((to, from, failure) => {
  const now = performance.now()
  navTimes = navTimes.filter((t) => now - t < 1000)
  navTimes.push(now)
  if (navTimes.length > 15) {
    navTimes = []
    showFatalError(
      'Похоже на redirect-цикл',
      new Error(`${from.fullPath} → ${to.fullPath}${failure ? `: ${failure.message}` : ''}`),
    )
  }
})

try {
  app.mount('#app')
} catch (err) {
  showFatalError('Не удалось запустить приложение', err)
}
