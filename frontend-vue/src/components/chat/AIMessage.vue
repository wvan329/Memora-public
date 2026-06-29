<template>
  <div class="flex">
    <div class="w-full bg-white dark:bg-gray-900 rounded-2xl px-2.5 py-1.5 shadow-sm border border-gray-100 dark:border-gray-700">
      <ThinkTools v-if="blocks.length > 0" :blocks="blocks" :default-open="thinkOpen" />
      <TextContent v-if="text" :raw="text" />
      <div v-if="blocks.length === 0 && !text" class="text-sm text-gray-400 dark:text-gray-500 italic">思考中…</div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import ThinkTools from './ThinkTools.vue'
import TextContent from './TextContent.vue'
import { useUIStore } from '@/stores/ui'
import type { ThinkBlock } from '@/stores/chat'

const props = defineProps<{ text: string; blocks: ThinkBlock[]; defaultOpen?: boolean }>()
const ui = useUIStore()

// 思考区默认展开：流式时强制展开，否则跟随全局设置
const thinkOpen = computed(() => props.defaultOpen || ui.thinkToolsOpen)
</script>
