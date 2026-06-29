<template>
  <Teleport to="body">
    <div v-if="auth.needsLogin"
      class="fixed inset-0 z-50 flex items-center justify-center bg-gray-900/60 dark:bg-black/70 backdrop-blur-sm">
      <div class="bg-white dark:bg-gray-900 rounded-2xl shadow-2xl p-8 w-full max-w-sm mx-4">
        <div class="text-center mb-6">
          <svg class="w-12 h-12 mx-auto mb-3 text-blue-600 dark:text-blue-400"
            fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2"
              d="M12 15v2m-6 4h12a2 2 0 002-2v-6a2 2 0 00-2-2H6a2 2 0 00-2 2v6a2 2 0 002 2zm10-10V7a4 4 0 00-8 0v4h8z"/>
          </svg>
          <h2 class="text-xl font-semibold text-gray-800 dark:text-gray-100">AI Chat 登录</h2>
          <p class="text-sm text-gray-500 dark:text-gray-400 mt-1">请输入访问密码</p>
        </div>
        <input v-model="pwd" type="password" placeholder="密码"
          @keydown.enter="doLogin"
          class="w-full border border-gray-300 dark:border-gray-600 rounded-xl px-4 py-2.5 text-sm
                 focus:ring-2 focus:ring-blue-400 focus:border-blue-400 outline-none transition mb-3
                 bg-white dark:bg-gray-800 text-gray-700 dark:text-gray-200 placeholder-gray-400 dark:placeholder-gray-500" />
        <div v-if="errorMsg" class="text-red-500 dark:text-red-400 text-xs mb-3 text-center">{{ errorMsg }}</div>
        <button @click="doLogin"
          class="w-full bg-blue-500 hover:bg-blue-600 text-white font-medium py-2.5 rounded-xl text-sm transition">
          登 录
        </button>
      </div>
    </div>
  </Teleport>
</template>

<script setup lang="ts">
import { ref } from 'vue'
import { useAuthStore } from '@/stores/auth'
import { useWebSocketStore } from '@/stores/websocket'
import { useSessionStore } from '@/stores/sessions'

const auth = useAuthStore()
const wsStore = useWebSocketStore()
const sessions = useSessionStore()

const pwd = ref('')
const errorMsg = ref('')

async function doLogin() {
  if (!pwd.value) { errorMsg.value = '请输入密码'; return }
  const result = await auth.login(pwd.value)
  if (!result.ok) {
    errorMsg.value = result.error || '密码错误'
    return
  }
  errorMsg.value = ''
  pwd.value = ''
  wsStore.connect()
  sessions.loadList()
}
</script>
