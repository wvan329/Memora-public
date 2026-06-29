<!-- ImageResult.vue — 渲染单图片类型的工具结果 -->
<template>
  <ImageCard :url="imgUrl" :filename="imgAlt" size="" @preview="previewVisible = true" />
  <ImageViewer :images="previewImages" :initial-index="0" :visible="previewVisible" @close="previewVisible = false" />
</template>

<script setup lang="ts">
import { ref, computed } from 'vue'
import type { ToolItemState } from '@/types/chat'
import { useAuthStore } from '@/stores/auth'
import ImageCard from '../ImageCard.vue'
import ImageViewer from '../ImageViewer.vue'
import type { ImageItem } from '../ImageViewer.vue'

const props = defineProps<{ item: ToolItemState }>()
const auth = useAuthStore()
const previewVisible = ref(false)

const imgUrl = computed(() => { try { const p=JSON.parse(props.item.result); if(p.type==='image') return (p.url||'')+'&password='+encodeURIComponent(auth.password) } catch{} return '' })
const imgAlt = computed(() => { try { return JSON.parse(props.item.result).filename||'' } catch{ return '' } })
const previewImages = computed<ImageItem[]>(() => [{ url: imgUrl.value, filename: imgAlt.value }])
</script>
