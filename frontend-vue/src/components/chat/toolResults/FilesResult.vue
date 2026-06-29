<!-- FilesResult.vue — 渲染多文件类型的工具结果 -->
<template>
  <div class="p-1.5">
    <template v-for="(f, i) in fileItems" :key="i">
      <a v-if="f.type === 'download'" :href="fileDownloadUrl(f)" :download="f.filename"
        class="block p-1.5 text-ai-tool dark:text-blue-400 underline cursor-pointer" @click.stop>
        📥 点击下载: {{ f.filename }}{{ fileSizeStr(f) }}
      </a>
      <ImageCard v-else-if="f.type === 'image'"
        :url="fileUrl(f)" :filename="f.filename" :size="fileSizeStr(f)" @preview="openFileImage(f)" />
      <div v-else class="text-red-500 text-xs">{{ f.error || '未知错误' }}</div>
    </template>
  </div>
  <ImageViewer :images="previewImages" :initial-index="previewIndex" :visible="previewVisible" @close="previewVisible = false" />
</template>

<script setup lang="ts">
import { ref, computed } from 'vue'
import type { ToolItemState, FileItem } from '@/types/chat'
import { useAuthStore } from '@/stores/auth'
import ImageCard from '../ImageCard.vue'
import ImageViewer from '../ImageViewer.vue'
import type { ImageItem } from '../ImageViewer.vue'
import { formatFileSize } from '@/utils/format'
import { resolveUrl } from '@/utils/url'

const props = defineProps<{ item: ToolItemState }>()
const auth = useAuthStore()
const previewVisible = ref(false)
const previewImages = ref<ImageItem[]>([])
const previewIndex = ref(0)

const fileItems = computed(() => {
  if (props.item.files?.length) return props.item.files
  try { const p=JSON.parse(props.item.result); if(p.type==='files'&&Array.isArray(p.items)) return p.items } catch{}
  return []
})
function fileUrl(f:FileItem):string { return (f.url||'')+'&password='+encodeURIComponent(auth.password) }
function fileDownloadUrl(f:FileItem):string { return (f.url||'')+'&password='+encodeURIComponent(auth.password) }
function fileSizeStr(f:FileItem):string { return f.size?formatFileSize(f.size):'' }

function openFileImage(f:FileItem) {
  const allImages = fileItems.value.filter((item: FileItem) => item.type === 'image')
  const idx = allImages.findIndex((item: FileItem) => item.url === f.url)
  previewImages.value = allImages.map((item: FileItem) => ({ url: resolveUrl(fileUrl(item)), filename: item.filename }))
  previewIndex.value = idx >= 0 ? idx : 0
  previewVisible.value = true
}
</script>
