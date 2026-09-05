import { createApp } from 'vue'

import App from './App.vue'
import router from './router'
import './styles/main.css'

const tg = window.Telegram?.WebApp
if (tg) {
  tg.ready()
  tg.expand()
}

createApp(App).use(router).mount('#app')
