<template>
  <div class="flex justify-center my-2">
    <div @click="toggleTruncate"
      class="system-msg truncatable w-full bg-gray-50 dark:bg-gray-800 border border-gray-200 dark:border-gray-700
             rounded-lg overflow-hidden relative"
      :class="{ 'truncated clamp-5': truncated }">
      <div class="px-3 py-2 text-sm text-gray-600 dark:text-gray-300 msg-content" v-html="rendered"></div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, watch } from 'vue'
import { renderMarkdown } from '@/utils/markdown'
import { useAuthStore } from '@/stores/auth'
import { useUIStore } from '@/stores/ui'

const props = defineProps<{ content: string }>()
const auth = useAuthStore()
const ui = useUIStore()
// 默认折叠；若全局设置"内容框默认展开"则不折叠
const truncated = ref(!ui.contentExpanded)
// 全局设置变化时实时更新
watch(() => ui.contentExpanded, (v) => { truncated.value = !v })

const rendered = computed(() => renderMarkdown(props.content || '', auth.password))

function toggleTruncate(e: Event) {
  if (!ui.allowToggle) return
  const target = e.target as HTMLElement; if (target.tagName === 'IMG') return
  /* 如果用户选中了文字，不切换折叠（让用户复制） */
  const sel = window.getSelection()
  if (sel && sel.toString().trim().length > 0) return
  truncated.value = !truncated.value
}
</script>