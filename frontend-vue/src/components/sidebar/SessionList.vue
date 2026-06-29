<template>
  <div class="flex-1 overflow-y-auto sidebar-full" ref="scrollContainer" @scroll="onScroll">
    <div v-if="!collapsed" class="px-3 py-2 hidden sm:block">
      <span class="text-xs text-gray-400 dark:text-gray-500 sidebar-label">最近会话</span>
    </div>
    <div class="flex flex-col">
      <div v-if="error" class="text-xs text-gray-400 dark:text-gray-500 text-center py-4">加载失败</div>
      <div v-else-if="!loading && sessionStore.pinnedSessions.length === 0 && sessionStore.unpinnedSessions.length === 0"
        class="text-xs text-gray-400 dark:text-gray-500 text-center py-4">暂无历史会话</div>

      <!-- 置顶区域 -->
      <template v-if="sessionStore.pinnedSessions.length > 0">
        <SessionItem v-for="s in sessionStore.pinnedSessions" :key="s.user_id"
          :session="s" :is-active="s.user_id === currentId" :pinned="true"
          @select="(id: string) => $emit('select', id)"
          @delete="(id: string) => $emit('delete', id)"
          @pin="(id: string) => sessionStore.togglePin(id)"
          @rename="(id: string, title: string) => sessionStore.renameSession(id, title)" />
        <div class="mx-3 border-t border-gray-100 dark:border-gray-700"></div>
      </template>

      <!-- 非置顶区域 -->
      <SessionItem v-for="s in sessionStore.unpinnedSessions" :key="s.user_id"
        :session="s" :is-active="s.user_id === currentId" :pinned="false"
        @select="(id: string) => $emit('select', id)"
        @delete="(id: string) => $emit('delete', id)"
        @pin="(id: string) => sessionStore.togglePin(id)"
        @rename="(id: string, title: string) => sessionStore.renameSession(id, title)" />

      <!-- 加载更多指示器 -->
      <div v-if="sessionStore.hasMore" class="text-center py-3">
        <span v-if="sessionStore.loading" class="text-xs text-gray-400 dark:text-gray-500">加载中…</span>
        <span v-else class="text-xs text-gray-400 dark:text-gray-500">滚动加载更多</span>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref } from 'vue'
import SessionItem from './SessionItem.vue'
import { useSessionStore } from '@/stores/sessions'

defineProps<{ currentId: string; loading: boolean; error: string; collapsed?: boolean }>()
defineEmits<{ select: [id: string]; delete: [id: string] }>()

const sessionStore = useSessionStore()
const scrollContainer = ref<HTMLElement | null>(null)

/** 滚动到底部附近时触发加载更多（阈值 60px） */
function onScroll() {
  const el = scrollContainer.value
  if (!el || !sessionStore.hasMore || sessionStore.loading) return
  const dist = el.scrollHeight - el.scrollTop - el.clientHeight
  if (dist < 60) {
    sessionStore.loadMore()
  }
}
</script>