<template>
  <div v-if="text"
    @mouseup="toggleTruncate"
    class="reasoning-block truncatable msg-content text-sm mt-0 px-2 py-0 rounded-lg overflow-x-auto break-words scrollbar-none
           text-ai-reason dark:text-green-400 bg-green-50/30 dark:bg-green-900/20 relative"
    :class="{ 'truncated clamp-5': truncated }" v-html="rendered"></div>
</template>

<script setup lang="ts">
import { ref, computed, watch } from 'vue'
import { renderMarkdown } from '@/utils/markdown'
import { useUIStore } from '@/stores/ui'

const props = defineProps<{ text: string }>()
const rendered = computed(() => renderMarkdown(props.text))

const ui = useUIStore()
// 默认折叠；若全局设置"内容框默认展开"则不折叠
const truncated = ref(!ui.contentExpanded)
// 全局设置变化时实时更新
watch(() => ui.contentExpanded, (v) => { truncated.value = !v })

function toggleTruncate(e: Event) {
  if (!ui.allowToggle) return
  /* 如果用户选中了文字，不切换折叠（让用户复制） */
  const sel = window.getSelection()
  if (sel && sel.toString().trim().length > 0) return
  truncated.value = !truncated.value
}
</script>