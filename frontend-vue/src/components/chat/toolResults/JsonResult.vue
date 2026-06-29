<!-- JsonResult.vue — 渲染 JSON / 普通文本类型的工具结果（含特殊工具名变体） -->
<template>
  <pre v-if="item.name === 'file_operation' || item.name === 'subprocess_exec'"
    class="text-sm whitespace-pre-wrap break-words">{{ item.result?.trim() }}</pre>
  <pre v-else-if="item.name === 'schedule_restart'"
    class="text-sm whitespace-pre-wrap break-words">{{ restartDisplay }}</pre>
  <div v-else class="msg-content" v-html="rendered"></div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import type { ToolItemState } from '@/types/chat'
import { renderMarkdown } from '@/utils/markdown'
import { useAuthStore } from '@/stores/auth'

const props = defineProps<{ item: ToolItemState }>()
const auth = useAuthStore()

const restartDisplay = computed(() => {
  const r = props.item.result
  if (!r) return ''
  try {
    const parsed = JSON.parse(r)
    if (parsed && typeof parsed === 'object' && typeof parsed.content === 'string') {
      return parsed.content
    }
  } catch { /* */ }
  return r
})

const rendered = computed(() => {
  const r = props.item.result
  return r ? renderMarkdown(r.trim(), auth.password).replace(/<p>\s*<\/p>/g, '') : ''
})
</script>
