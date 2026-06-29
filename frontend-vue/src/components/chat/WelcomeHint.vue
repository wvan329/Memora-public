<template>
  <div class="flex flex-col items-center justify-center mt-20 sm:mt-28" data-no-quote>
    <!-- Logo -->
    <img src="/favicon.png" alt="Memora" class="w-14 h-14 mb-6 opacity-80" />

    <!-- 灵性话语（AI 流式生成，Markdown 渲染） -->
    <div
      class="text-sm text-gray-400 dark:text-gray-500 font-light px-6 max-w-xs text-center msg-content [&_p]:m-0"
      v-html="welcomeText ? renderedWelcome : '…'"
    />

    <!-- 分隔线 -->
    <div class="w-10 h-px bg-gray-200 dark:bg-gray-700 mt-6"></div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, watch, onMounted, onUnmounted } from 'vue'
import { useChatStore } from '@/stores/chat'
import { apiPath, authHeaders } from '@/utils/api'
import { useAuthStore } from '@/stores/auth'
import { renderMarkdown } from '@/utils/markdown'

const chat = useChatStore()
const auth = useAuthStore()
const welcomeText = ref('')
const renderedWelcome = computed(() => renderMarkdown(welcomeText.value))
let abortCtrl: AbortController | null = null

async function fetchWelcome() {
  abortCtrl?.abort()
  abortCtrl = new AbortController()
  welcomeText.value = ''

  try {
    const url = new URL(apiPath('/api/welcome'), window.location.href).href
    const resp = await fetch(url, { signal: abortCtrl.signal, headers: authHeaders(auth.password) })
    const reader = resp.body?.getReader()
    if (!reader) return

    const decoder = new TextDecoder()
    let buffer = ''

    while (true) {
      const { done, value } = await reader.read()
      if (done) break
      buffer += decoder.decode(value, { stream: true })

      const lines = buffer.split('\n')
      buffer = lines.pop() || ''

      for (const line of lines) {
        if (line.startsWith('data: ')) {
          const data = line.slice(6)
          if (data === '[DONE]') return
          welcomeText.value += data
        }
      }
    }
  } catch {
    // 请求取消或网络错误 → 静默
  }
}

// history 确认空会话后触发
watch(() => chat.welcomeSeed, () => { fetchWelcome() })

// 组件挂载时如果 seed 已有值（重连场景），直接触发
onMounted(() => {
  if (chat.welcomeSeed > 0) fetchWelcome()
})

onUnmounted(() => {
  abortCtrl?.abort()
})
</script>