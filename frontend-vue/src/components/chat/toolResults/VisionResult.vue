<!-- VisionResult.vue — 渲染视觉识别类型的工具结果 -->
<template>
  <div class="p-1.5">
    <ImageCard v-for="(img, i) in visionImages" :key="i"
      :url="img.url" :filename="''" :size="img.size" @preview="openVisionImage(i)" />
    <div class="text-sm mt-1 text-gray-500 dark:text-gray-400 msg-content" v-html="rendered"></div>
  </div>
  <ImageViewer :images="previewImages" :initial-index="previewIndex" :visible="previewVisible" @close="previewVisible = false" />
</template>

<script setup lang="ts">
import { ref, computed } from 'vue'
import type { ToolItemState, VisionImage } from '@/types/chat'
import { renderMarkdown } from '@/utils/markdown'
import { useAuthStore } from '@/stores/auth'
import ImageCard from '../ImageCard.vue'
import ImageViewer from '../ImageViewer.vue'
import type { ImageItem } from '../ImageViewer.vue'
import { resolveUrl } from '@/utils/url'

const props = defineProps<{ item: ToolItemState }>()
const auth = useAuthStore()
const previewVisible = ref(false)
const previewImages = ref<ImageItem[]>([])
const previewIndex = ref(0)

const visionImages = computed(() => {
  if (props.item.images?.length) return props.item.images
  try { const p = JSON.parse(props.item.result); if (p.type === 'vision_result' && Array.isArray(p.images)) return p.images } catch { /* */ }
  return [] as VisionImage[]
})

const rendered = computed(() => {
  const r = props.item.result
  return r ? renderMarkdown(r.trim(), auth.password).replace(/<p>\s*<\/p>/g, '') : ''
})

function openVisionImage(i: number | string) {
  const idx = typeof i === 'string' ? parseInt(i) : i
  const allImages = visionImages.value
  previewImages.value = allImages.map((img: VisionImage) => ({ url: resolveUrl(img.url), filename: '' }))
  previewIndex.value = idx >= 0 && idx < allImages.length ? idx : 0
  previewVisible.value = true
}
</script>
