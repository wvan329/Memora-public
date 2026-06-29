<template>
  <div class="px-3 py-3 border-t border-gray-100 dark:border-gray-800">
    <!-- 展开状态 -->
    <button v-if="!collapsed" @click="$emit('toggle')"
      class="sidebar-full w-full text-xs font-medium px-3 py-1.5 rounded-lg transition flex items-center justify-center gap-1.5"
      :class="autoScroll ? 'bg-blue-500 text-white' : 'bg-gray-100 dark:bg-gray-700 text-gray-400 dark:text-gray-500'">
      <svg class="w-3.5 h-3.5 flex-shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2"
          :d="autoScroll ? 'M18 15l-6-6-6 6' : 'M6 9l6 6 6-6'" />
      </svg>
      <span>自动滚动: {{ autoScroll ? '开' : '关' }}</span>
    </button>
    <!-- 折叠状态 -->
    <button v-else @click="$emit('toggle')"
      class="sidebar-icon-only flex items-center justify-center w-full py-1.5 text-gray-400 dark:text-gray-500 hover:text-gray-600 dark:hover:text-gray-300 transition"
      :title="'自动滚动: ' + (autoScroll ? '开' : '关')">
      <svg class="w-4 h-4 flex-shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2"
          :d="autoScroll ? 'M18 15l-6-6-6 6' : 'M6 9l6 6 6-6'" />
      </svg>
    </button>
  </div>
</template>

<script setup lang="ts">
defineProps<{ autoScroll: boolean; collapsed: boolean }>()
defineEmits<{ toggle: [] }>()
</script>
