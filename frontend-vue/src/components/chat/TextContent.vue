<template>
  <div
    class="text-content msg-content text-sm leading-snug text-ai-text dark:text-gray-300"
    v-html="rendered"
    @click="onClick"
  />
  <ImageViewer
    :images="previewImages"
    :initial-index="previewIndex"
    :visible="previewVisible"
    @close="previewVisible = false"
  />
</template>

<script setup lang="ts">
import { ref, computed } from 'vue'
import { renderMarkdown } from '@/utils/markdown'
import { useAuthStore } from '@/stores/auth'
import ImageViewer from './ImageViewer.vue'
import type { ImageItem } from './ImageViewer.vue'
import { resolveUrl } from '@/utils/url'

const props = defineProps<{ raw: string }>()
const auth = useAuthStore()

const rendered = computed(() => renderMarkdown(props.raw, auth.password))

const previewVisible = ref(false)
const previewImages = ref<ImageItem[]>([])
const previewIndex = ref(0)

function onClick(e: MouseEvent) {
  const target = e.target as HTMLElement
  if (target.tagName !== 'IMG') return
  const img = target as HTMLImageElement
  // 收集当前容器内所有图片
  const allImgs = (e.currentTarget as HTMLElement).querySelectorAll('img')
  const urls: ImageItem[] = []
  let idx = -1
  allImgs.forEach((el, i) => {
    if (el === img) idx = urls.length
    // 只收集有实际内容的图片（排除太小或装饰性的）
    if (el.src && el.naturalWidth > 0) {
      urls.push({ url: resolveUrl(el.src), filename: el.alt || '' })
    }
  })
  if (urls.length === 0) return
  previewImages.value = urls
  previewIndex.value = idx >= 0 ? idx : 0
  previewVisible.value = true
}
</script>
