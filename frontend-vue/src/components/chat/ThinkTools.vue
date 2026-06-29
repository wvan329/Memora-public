<template>
  <div v-if="hasContent" class="think-tools-container mb-0 space-y-0 leading-none">
    <div @click="toggle"
         class="text-sm font-medium text-gray-500 dark:text-gray-400 cursor-default"
         role="button" :aria-expanded="isOpen">
      💭 思考
    </div>
    <div v-show="isOpen" class="think-tools-content -mt-0.5 [&>*]:!py-0 [&>*]:!mt-0 [&>*]:!mb-0">
      <template v-for="(block, bi) in blocks" :key="bi">
        <ReasoningBlock v-if="block.type === 'reasoning'" :text="block.text" />
        <ToolCard v-else-if="block.type === 'tool'" :item="block.item" :idx="0" />
      </template>
    </div>
    <div v-show="isOpen" class="text-left -mt-0.5">
      <button @click.stop="close" class="text-xs text-gray-400 dark:text-gray-500 hover:text-gray-600 dark:hover:text-gray-300 transition">
        收起 ▲
      </button>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useUIStore } from '@/stores/ui'
import ReasoningBlock from './ReasoningBlock.vue'
import ToolCard from './ToolCard.vue'
import type { ThinkBlock } from '@/stores/chat'

const ui = useUIStore()

const props = defineProps<{ blocks: ThinkBlock[]; defaultOpen?: boolean }>()
// 初始值由 defaultOpen 决定（流式时为 true，历史时为 false）。
// 之后不随 defaultOpen 变化——用户手动操作或 abort 不改变折叠状态。
const isOpen = ref(props.defaultOpen ?? ui.thinkToolsOpen)
// 全局设置变化时实时更新
watch(() => ui.thinkToolsOpen, (v) => { isOpen.value = props.defaultOpen || v })

const hasContent = computed(() => props.blocks.length > 0)

function toggle() { isOpen.value = !isOpen.value }
function close() { isOpen.value = false }
</script>