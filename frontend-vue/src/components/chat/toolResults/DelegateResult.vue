<!-- DelegateResult.vue — 渲染委托类型的工具结果 -->
<template>
  <div class="msg-content" v-html="rendered"></div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import type { ToolItemState } from '@/types/chat'
import { renderMarkdown } from '@/utils/markdown'
import { useAuthStore } from '@/stores/auth'

const props = defineProps<{ item: ToolItemState }>()
const auth = useAuthStore()

const delegateDisplay = computed(() => {
  if (!props.item.result) return ''
  try {
    const p = JSON.parse(props.item.result)
    if (p.error) return `❌ ${p.error}`
    if (p.sessions && Array.isArray(p.sessions)) {
      const sessions = p.sessions as Array<Record<string, unknown>>
      const total = sessions.length
      const succeeded = sessions.filter((s: Record<string, unknown>) => s.success).length
      const failed = total - succeeded
      let summary = `📋 批量委托完成：${succeeded}/${total} 个子任务成功`
      if (failed > 0) summary += `，${failed} 个失败`
      return summary
    }
    if (p.type === 'vision_result') {
      const imgCount = (p.images as Array<unknown> | undefined)?.length || 0
      const textLen = (p.text as string | undefined)?.length || 0
      return `🖼️ 识图完成：${imgCount} 张图片，${textLen} 字分析结果`
    }
    return p.result || JSON.stringify(p, null, 2)
  } catch { return props.item.result }
})

const rendered = computed(() => {
  const t = delegateDisplay.value
  return t ? renderMarkdown(t.trim(), auth.password).replace(/<p>\s*<\/p>/g, '') : ''
})
</script>
