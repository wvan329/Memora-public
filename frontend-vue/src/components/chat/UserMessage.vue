<template>
  <div class="flex justify-end user-msg-wrapper" :data-turn-id="turnId" :data-msg-id="msgId" :class="{ 'opacity-70': isOptimistic }">
    <div @click="toggleTruncate"
      class="user-msg-content truncatable w-2/3 max-md:w-full bg-gray-900 dark:bg-gray-400 text-white dark:text-gray-900
             rounded-2xl px-4 py-2.5 text-sm shadow-sm border border-gray-700 dark:border-gray-500
             cursor-default relative flex items-start gap-2"
      :class="{ 'truncated clamp-5': truncated }">
      <div class="msg-content whitespace-pre-wrap break-words flex-1 min-w-0">{{ text }}</div>
      <!-- ⋮ 菜单按钮——固定在消息框内最右侧 -->
      <div class="relative flex-shrink-0">
        <button @click.stop="openMenu"
          class="p-0.5 rounded text-gray-400 dark:text-gray-500 hover:text-gray-600 dark:hover:text-gray-300 transition"
          title="更多操作">
          <svg class="w-4 h-4" fill="currentColor" viewBox="0 0 24 24">
            <circle cx="12" cy="5" r="1.5"/><circle cx="12" cy="12" r="1.5"/><circle cx="12" cy="19" r="1.5"/>
          </svg>
        </button>
        <Teleport to="body">
          <div v-if="menuOpen" class="fixed z-[110] bg-white dark:bg-gray-800 border border-gray-200 dark:border-gray-600
                        rounded-lg shadow-lg py-1 min-w-[110px]"
            :style="{ left: menuX + 'px', top: menuY + 'px' }" @click.stop>
            <button @click="handleFork"
              class="w-full text-left px-3 py-1.5 text-xs text-gray-700 dark:text-gray-200 hover:bg-indigo-50 dark:hover:bg-indigo-900/30 transition">
              🔀 分叉
            </button>
            <button @click="handleDelete"
              class="w-full text-left px-3 py-1.5 text-xs text-gray-700 dark:text-gray-200 hover:bg-red-50 dark:hover:bg-red-900/30 transition">
              🗑️ 删除
            </button>

            <!-- 所有提问已移至底部三点按钮 -->
          
          </div>
        </Teleport>
        <!-- 点击外部关闭 -->
        <div v-if="menuOpen" class="fixed inset-0 z-[100]" @click="menuOpen = false"></div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, watch } from 'vue'
import { useUIStore } from '@/stores/ui'
import { useSessionStore } from '@/stores/sessions'
import { useChatStore } from '@/stores/chat'
import { useAuthStore } from '@/stores/auth'
import { useWebSocketStore } from '@/stores/websocket'
import { apiPath } from '@/utils/api'


const props = defineProps<{ text: string; turnId?: string; isOptimistic?: boolean; msgId?: string }>()

const ui = useUIStore()
const sessions = useSessionStore()
const chat = useChatStore()
const auth = useAuthStore()
const wsStore = useWebSocketStore()

// 默认折叠；若全局设置"内容框默认展开"则不折叠
const truncated = ref(!ui.contentExpanded)
// 全局设置变化时实时更新
watch(() => ui.contentExpanded, (v) => { truncated.value = !v })
const menuOpen = ref(false)
const menuX = ref(0)
const menuY = ref(0)

function toggleTruncate(e: Event) {
  if (!ui.allowToggle) return
  /* 如果用户选中了文字，不切换折叠（让用户复制） */
  const sel = window.getSelection()
  if (sel && sel.toString().trim().length > 0) return
  truncated.value = !truncated.value
}

function openMenu(e: MouseEvent) {
  const btn = e.currentTarget as HTMLElement
  const rect = btn.getBoundingClientRect()
  menuX.value = rect.right
  menuY.value = rect.bottom + 2
  menuOpen.value = true
}

async function handleFork() {
  menuOpen.value = false
  if (!props.turnId) return
  ui.showDialog({
    message: '确定要从此处分叉到新会话吗？',
    confirmText: '分叉',
    cancelText: '取消',
    showInput: false,
    callback: async (result) => {
      if (!result) return
      try {
        const resp = await fetch(apiPath('/api/fork'), {
          method: 'POST',
          headers: { 'Content-Type': 'application/json', 'X-Access-Password': auth.password },
          body: JSON.stringify({ session_id: chat.sessionId, turn_id: props.turnId })
        })
        if (!resp.ok) throw new Error('分叉失败')
        const data = await resp.json()
        chat.switchSession(data.new_session_id)
        wsStore.disconnect()
        wsStore.connect()
        sessions.loadList()
      } catch (_err) { /* 静默失败 */ }
    }
  })
}

function handleDelete() {
  menuOpen.value = false
  if (!props.turnId) return
  ui.showDialog({
    message: '确定删除本轮对话吗？',
    confirmText: '删除',
    cancelText: '取消',
    showInput: false,
    callback: (result) => { if (result) sessions.deleteTurn(props.turnId!) }
  })
}
</script>
