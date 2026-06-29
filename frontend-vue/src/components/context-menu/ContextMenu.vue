<template>
  <Teleport to="body">
    <div v-if="menu" :style="{ left: menu.x + 'px', top: menu.y + 'px' }"
      class="fixed z-[100] flex gap-1.5 bg-white dark:bg-gray-800 border border-gray-200 dark:border-gray-600
             rounded-xl shadow-2xl p-1 select-none backdrop-blur-sm"
      id="ctxMenu">
      <!-- ⭐ 引用：选中文字时显示（所有消息类型） -->
      <button v-if="menu.type === 'quote'" @click="handleQuote"
        class="flex items-center gap-1 px-2 py-1 text-sm font-medium text-gray-700 dark:text-gray-200
               hover:bg-yellow-50 dark:hover:bg-yellow-900/30 rounded-lg transition">
        <span class="text-base">⭐</span>
      </button>
      <!-- 💾 保存图片 -->
      <button v-if="menu.type === 'image'" @click="handleSaveImage"
        class="flex items-center gap-1.5 px-3 py-2 text-sm font-medium text-gray-700 dark:text-gray-200
               hover:bg-blue-50 dark:hover:bg-blue-900/30 rounded-lg transition">
        <span class="text-base">💾</span> 保存
      </button>
    </div>
  </Teleport>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { useUIStore } from '@/stores/ui'
import { useChatStore } from '@/stores/chat'

const ui = useUIStore()
const chat = useChatStore()

const menu = computed(() => ui.contextMenu)

function handleQuote() {
  const m = menu.value
  if (!m) return
  const sel = window.getSelection()
  const text = (sel && sel.rangeCount > 0 && sel.toString().trim()) ? sel.toString().trim() : m.savedText
  if (!text) { ui.hideContextMenu(); return }
  const textarea = document.querySelector('footer textarea') as HTMLTextAreaElement | null
  if (textarea) {
    const prefix = '针对上文提到的【' + text + '】：'
    const start = textarea.selectionStart
    const end = textarea.selectionEnd
    textarea.value = textarea.value.slice(0, start) + prefix + textarea.value.slice(end)
    textarea.selectionStart = textarea.selectionEnd = start + prefix.length
    textarea.dispatchEvent(new Event('input', { bubbles: true }))
    ui.hideContextMenu()
    requestAnimationFrame(() => textarea.focus())
  }
  window.getSelection()?.removeAllRanges()
}

function handleSaveImage() {
  const m = menu.value
  if (!m || !m.imageSrc) return
  try {
    const a = document.createElement('a'); a.href = m.imageSrc; a.download = ''
    document.body.appendChild(a); a.click(); document.body.removeChild(a)
  } catch { window.open(m.imageSrc, '_blank') }
  ui.hideContextMenu()
}

// ── 选中文字检测 ──
// 仅用于 ⭐ 引用按钮——用户消息的 🔀/🗑️/📋 已迁移到 UserMessage 的 ⋮ 菜单
import { onMounted, onUnmounted } from 'vue'

let selDebounceTimer: ReturnType<typeof setTimeout> | null = null

function checkTextSelection(isMouseUp: boolean) {
  const sel = window.getSelection()
  if (!sel || !sel.toString().trim() || !sel.rangeCount) return

  const text = sel.toString().trim()
  if (!text) return

  const anchorNode = sel.anchorNode
  if (!anchorNode) return
  // 封面欢迎语不弹出引用按钮
  const anchorEl = anchorNode.nodeType === 3 ? anchorNode.parentElement : anchorNode as Element
  if (anchorEl?.closest?.('[data-no-quote]')) return
  const messagesEl = document.getElementById('messagesWrapper')
  if (!messagesEl || !messagesEl.contains(anchorNode)) return

  if (chat.isStreaming) {
    const currentEl = messagesEl.lastElementChild
    if (currentEl && currentEl.contains(anchorNode)) return
  }

  if (isMouseUp) {
    chat.flushPausedChunks()
  }

  const range = sel.getRangeAt(0)
  const rect = range.getBoundingClientRect()

  ui.showContextMenu({
    x: rect.left + rect.width / 2 - 40,
    y: rect.bottom + 8,
    type: 'quote',
    savedText: text,
  })
}

function onMouseUp() {
  setTimeout(() => checkTextSelection(true), 10)
}

function onSelectionChange() {
  if (selDebounceTimer) clearTimeout(selDebounceTimer)
  selDebounceTimer = setTimeout(() => checkTextSelection(false), 350)
}

function onClickOutside(e: MouseEvent | TouchEvent) {
  const el = document.getElementById('ctxMenu')
  if (el && !el.contains(e.target as Node)) ui.hideContextMenu()
}

function onScrollHide(e?: Event) {
  // 如果触摸/点击在菜单内，不关闭（手机端点击 ⭐ 按钮时 touchstart 先触发）
  if (e && e.target instanceof Node) {
    const el = document.getElementById('ctxMenu')
    if (el && el.contains(e.target)) return
  }
  if (ui.contextMenu) ui.hideContextMenu()
}

onMounted(() => {
  document.addEventListener('mouseup', onMouseUp)
  document.addEventListener('touchend', onMouseUp)  // 手机端选中文字后触发
  document.addEventListener('selectionchange', onSelectionChange)
  document.addEventListener('mousedown', onClickOutside)
  document.addEventListener('touchstart', onScrollHide, { passive: true })
  document.getElementById('chatContainer')?.addEventListener('scroll', onScrollHide, { passive: true })
})

onUnmounted(() => {
  document.removeEventListener('mouseup', onMouseUp)
  document.removeEventListener('touchend', onMouseUp)
  document.removeEventListener('selectionchange', onSelectionChange)
  document.removeEventListener('mousedown', onClickOutside)
  document.removeEventListener('touchstart', onScrollHide)
  document.getElementById('chatContainer')?.removeEventListener('scroll', onScrollHide)
})
</script>
