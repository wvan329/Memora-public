<template>
  <div class="flex flex-col flex-1 min-w-0">
    <main ref="containerRef" id="chatContainer" class="flex-1 overflow-y-auto px-4 pt-6 pb-2" @wheel="onWheel" @scroll="onScrollUpdate" @click="onBlankClick">
      <div id="messagesWrapper" class="max-w-5xl mx-auto space-y-2"><MessageList /></div>
    </main>
    <!-- 按钮锚点：紧贴 InputArea 上方的 wrapper，absolute 定位在 wrapper 之上 -->
    <div class="relative max-w-5xl mx-auto w-full">
      <div class="absolute bottom-full right-2 pb-2 pointer-events-none flex flex-col items-end gap-1.5">
        <!-- 待发送指示器：后端忙时发送的消息不显示气泡，而是在此处显示 -->
        <div v-if="chat.pendingBufferMsg" class="pointer-events-auto flex items-center gap-1">
          <button @click="togglePending"
            class="text-xs px-2 py-1 rounded-lg bg-amber-50 dark:bg-amber-900/30 border border-amber-200 dark:border-amber-700 text-amber-700 dark:text-amber-300 hover:bg-amber-100 dark:hover:bg-amber-900/50 transition"
            :class="pendingExpanded ? 'max-w-[22rem] whitespace-normal text-left' : 'max-w-10 truncate'"
            :title="pendingExpanded ? '' : '点击查看待发送消息'">
            ⏳{{ pendingExpanded ? ' ' + chat.pendingBufferMsg : '' }}
          </button>
          <button @click="discardPending"
            class="text-xs w-5 h-5 flex items-center justify-center rounded-full bg-amber-100 dark:bg-amber-800/40 text-amber-500 dark:text-amber-400 hover:bg-red-100 dark:hover:bg-red-900/40 hover:text-red-500 dark:hover:text-red-400 transition"
            title="丢弃这条消息">
            ✕
          </button>
        </div>
        <ScrollToBottom v-if="scrollMode !== 'none'" :mode="scrollMode"
          class="pointer-events-auto"
          @jump="jumpToBottom" @nav="showMessageNav" />
      </div>
    </div>
    <InputArea />
  </div>
</template>

<script setup lang="ts">
import { ref, computed, nextTick, watch, onMounted } from 'vue'
import MessageList from './MessageList.vue'
import ScrollToBottom from '@/components/common/ScrollToBottom.vue'
import InputArea from '@/components/input/InputArea.vue'
import { useAutoScroll } from '@/composables/useAutoScroll'
import { useChatStore } from '@/stores/chat'
import { useWebSocketStore } from '@/stores/websocket'
import { useUIStore } from '@/stores/ui'

const chat = useChatStore()
const wsStore = useWebSocketStore()
const ui = useUIStore()
const containerRef = ref<HTMLElement | null>(null)
const { scrollToBottom, onWheel, onScroll, jumpToBottom } = useAutoScroll(containerRef)
const distanceFromBottom = ref(0)
const pendingExpanded = ref(false)

function togglePending() {
  pendingExpanded.value = !pendingExpanded.value
}

function onBlankClick(e: MouseEvent) {
  if (chat.messages.length !== 0) return
  const el = e.target as HTMLElement
  // 点击交互元素时不触发
  if (el.closest('textarea, button, a, input, [role="button"]')) return
  // 输入法激活中不触发（点击空白通常是为了关闭输入法）
  if (ui.isComposing) return
  // WelcomeHint 内容区（分隔线以上）不触发，仅真正的空白区域触发
  if (el.closest('[data-no-quote]')) return
  chat.welcomeSeed++
}

function discardPending() {
  // 通知 InputArea 把消息回显到输入框
  chat.discardedBufferMsg = chat.pendingBufferMsg
  chat.pendingBufferMsg = null
  pendingExpanded.value = false
  wsStore.send({ type: 'discard_buffer', session_id: chat.sessionId })
}

// 滚动模式：远离底部 → 跳回；靠近底部 → 三点导航
// 流式输出时也正常显示按钮（用户可能想导航历史消息）
const scrollMode = computed(() => {
  if (chat.messages.length === 0) return 'none' as const
  if (distanceFromBottom.value > 80) return 'jump' as const
  return 'nav' as const
})

function updateDistance() { const el = containerRef.value; if (!el) return; distanceFromBottom.value = el.scrollHeight - el.scrollTop - el.clientHeight }
function onScrollUpdate() { onScroll(); updateDistance() }

function showMessageNav() {
  const userMsgs = chat.messages.filter(m => m.role === 'user')
  if (userMsgs.length === 0) return
  const options = userMsgs.map(m => m.content.slice(0, 50) + (m.content.length > 50 ? '…' : ''))
  const dialogId = ui.showDialog({
    message: '所有提问（共 ' + userMsgs.length + ' 条）',
    options,
    showInput: false,
    resolveOnSelect: true,  // 点选项直接跳转，不需要确认按钮
    hideActions: true,      // 隐藏底部取消/确认按钮
    callback: (result: any) => {
      const selected = result?.selected || result?.answers?.[0]?.selected
      if (selected) {
        const idx = options.indexOf(selected)
        if (idx >= 0) {
          requestAnimationFrame(() => {
            const el = document.querySelector(`[data-msg-id="${userMsgs[idx].id}"]`)
            if (el) el.scrollIntoView({ block: 'center' })
          })
        }
      }
    }
  })
  // 弹窗打开后强制滚动到底部（最近的消息）
  nextTick(() => {
    requestAnimationFrame(() => {
      const scrollEl = document.querySelector(`[data-dialog-id="${dialogId}"] .scrollbar-thin`)
      if (scrollEl) {
        scrollEl.scrollTop = scrollEl.scrollHeight
      }
    })
  })
}

function doScroll() {
  nextTick(() => {
    requestAnimationFrame(() => {
      scrollToBottom()
      updateDistance()
    })
  })
}

// 流式增量 → onChunk 回调（store 每次突变时同步触发）
chat.onChunk(doScroll)
// 历史全量加载 → watch（loadHistory 不经过 onChunk）
watch(() => chat.messages.length, doScroll)

// 页面加载时从后端获取通知开关状态、高精度设置和浏览器模式
onMounted(() => { ui.fetchNotificationStatus(); ui.fetchVisionHighRes(); ui.fetchBrowserHeaded() })
</script>