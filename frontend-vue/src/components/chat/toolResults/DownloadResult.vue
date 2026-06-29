<!-- DownloadResult.vue — 渲染下载类型的工具结果 -->
<template>
  <a :href="downloadUrl" :download="downloadFilename"
    class="block p-1.5 text-ai-tool dark:text-blue-400 underline cursor-pointer" @click.stop>
    📥 点击下载: {{ downloadFilename }}{{ downloadSize }}
  </a>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import type { ToolItemState } from '@/types/chat'
import { useAuthStore } from '@/stores/auth'
import { formatFileSize } from '@/utils/format'

const props = defineProps<{ item: ToolItemState }>()
const auth = useAuthStore()

const downloadUrl = computed(() => { try { const p=JSON.parse(props.item.result); if(p.type==='download') return (p.url||'')+'&password='+encodeURIComponent(auth.password) } catch{} return '#' })
const downloadFilename = computed(() => { try { return JSON.parse(props.item.result).filename||'download' } catch{ return 'download' } })
const downloadSize = computed(() => { try { const p=JSON.parse(props.item.result); return p.size?formatFileSize(p.size):'' } catch{ return '' } })
</script>
