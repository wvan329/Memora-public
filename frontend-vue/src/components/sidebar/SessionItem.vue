<template>
  <div class="session-item px-3 py-2 border-b border-gray-50 dark:border-gray-800 flex items-center gap-1
              transition-colors relative group"
    :class="[
      // 置顶始终琥珀色左边框
      pinned ? 'border-l-[3px] border-l-amber-400 dark:border-l-amber-500' : '',
      // 非置顶选中：蓝色左边框
      !pinned && isActive ? 'border-l-[3px] border-l-black dark:border-l-gray-300' : '',
      // 选中：蓝色背景（置顶/非置顶统一）
      isActive ? 'bg-blue-50 dark:bg-blue-950/40' : '',
      // 置顶未选中：浅琥珀背景
      pinned && !isActive ? 'bg-amber-50/50 dark:bg-amber-900/20' : '',
      // 非置顶未选中：灰色 hover
      !pinned && !isActive ? 'hover:bg-gray-100 dark:hover:bg-gray-800' : ''
    ]">
    <div class="flex-1 min-w-0 cursor-default" @click="$emit('select', session.user_id)">
      <span class="text-xs text-gray-700 dark:text-gray-200 block truncate">
        <span v-if="pinned" class="text-amber-500 mr-1" title="已置顶">📌</span>{{ displayTitle }}
      </span>
      <span class="text-[10px] text-gray-400 dark:text-gray-500">{{ formatTime(session.last_time) }}</span>
    </div>
    <!-- ⋮ 菜单按钮 -->
    <div class="relative flex-shrink-0">
      <button @click.stop="menuOpen = !menuOpen"
        class="flex-shrink-0 text-gray-500 dark:text-gray-400 hover:text-gray-700 dark:hover:text-gray-200 p-0.5 rounded transition"
        title="更多操作">
        <svg class="w-4 h-4" fill="currentColor" viewBox="0 0 24 24">
          <circle cx="12" cy="5" r="1.5"/><circle cx="12" cy="12" r="1.5"/><circle cx="12" cy="19" r="1.5"/>
        </svg>
      </button>
      <!-- 弹出菜单 -->
      <div v-if="menuOpen" class="absolute right-0 top-full mt-1 z-30 bg-white dark:bg-gray-800 border border-gray-200 dark:border-gray-600
                    rounded-lg shadow-lg py-0 min-w-[100px]"
        @click.stop>
        <button @click="$emit('pin', session.user_id); menuOpen = false"
          class="w-full text-left px-2 py-0.5 text-xs text-gray-700 dark:text-gray-200 hover:bg-amber-50 dark:hover:bg-amber-900/30 transition">
          {{ pinned ? '取消置顶' : '置顶' }}
        </button>
        <button @click="handleRename(); menuOpen = false"
          class="w-full text-left px-2 py-0.5 text-xs text-gray-700 dark:text-gray-200 hover:bg-blue-50 dark:hover:bg-blue-900/30 transition">
          重命名
        </button>
        <button @click="$emit('delete', session.user_id); menuOpen = false"
          class="w-full text-left px-2 py-0.5 text-xs text-gray-700 dark:text-gray-200 hover:bg-red-50 dark:hover:bg-red-900/30 transition">
          删除
        </button>

      </div>
    </div>
    <!-- 点击菜单外关闭 -->
    <div v-if="menuOpen" class="fixed inset-0 z-20" @click="menuOpen = false"></div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed } from 'vue'
import { useUIStore } from '@/stores/ui'
import type { SessionSummary } from '@/types/chat'
import { formatTime } from '@/utils/markdown'

const props = defineProps<{ session: SessionSummary; isActive: boolean; pinned: boolean }>()
const emit = defineEmits<{ select: [id: string]; delete: [id: string]; pin: [id: string]; rename: [id: string, title: string] }>()

const ui = useUIStore()
const menuOpen = ref(false)

// display_title 由后端计算：custom_title || 第一条用户消息 || '(空会话)'
const displayTitle = computed(() => props.session.display_title || props.session.custom_title || props.session.title || '(空会话)')

function handleRename() {
  const sessionId = props.session.user_id
  const currentName = props.session.custom_title || props.session.title || ''
  // 使用 UI store 的 showDialog 弹出带输入框的弹窗
  ui.showDialog({
    message: '请输入新的会话名称',
    confirmText: '保存',
    cancelText: '取消',
    showInput: true,
    inputValue: currentName,
    callback: (result) => {
      if (!result) return
      // result.answers: [{ selected, text }]
      const answer = result.answers?.[0]
      const newTitle = (answer?.text || '').trim()
      if (newTitle && newTitle !== currentName) {
        emit('rename', sessionId, newTitle)
      }
    }
  })
}


</script>