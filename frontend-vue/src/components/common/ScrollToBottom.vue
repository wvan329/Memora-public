<template>
  <!-- 远离底部：跳回底部按钮 -->
  <button v-if="mode === 'jump'"
    @click="$emit('jump')"
    class="w-[34px] h-[34px] bg-white/90 dark:bg-gray-800/90 backdrop-blur-sm rounded-full shadow-[0_1px_4px_rgba(0,0,0,0.12)] dark:shadow-[0_1px_4px_rgba(0,0,0,0.3)] flex items-center justify-center z-30 transition text-gray-500 dark:text-gray-400 hover:text-blue-600 dark:hover:text-blue-400 border-none cursor-pointer"
    title="跳回底部">
    <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
      <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 14l-7 7m0 0l-7-7m7 7V3"/>
    </svg>
  </button>

  <!-- 靠近底部：三点导航按钮 -->
  <button v-else-if="mode === 'nav'"
    @click="$emit('nav')"
    class="w-[34px] h-[34px] bg-white/90 dark:bg-gray-800/90 backdrop-blur-sm rounded-full shadow-[0_1px_4px_rgba(0,0,0,0.12)] dark:shadow-[0_1px_4px_rgba(0,0,0,0.3)] flex items-center justify-center z-30 transition text-gray-500 dark:text-gray-400 hover:text-gray-700 dark:hover:text-gray-300 border-none cursor-pointer"
    title="消息导航">
    <svg class="w-5 h-5" fill="currentColor" viewBox="0 0 24 24">
      <circle cx="12" cy="5" r="1.5"/><circle cx="12" cy="12" r="1.5"/><circle cx="12" cy="19" r="1.5"/>
    </svg>
  </button>
</template>

<script setup lang="ts">
defineEmits<{ jump: []; nav: [] }>()
defineProps<{ mode: 'jump' | 'nav' | 'none' }>()
</script>
