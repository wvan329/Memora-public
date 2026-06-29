<template>
  <Transition name="toast-fade">
    <div v-if="ui.toastVisible"
      class="fixed top-4 left-1/2 -translate-x-1/2 z-[100] px-4 py-2 bg-indigo-700 text-white rounded-lg shadow-lg text-sm pointer-events-none">
      {{ ui.toastMessage }}
    </div>
  </Transition>
</template>

<script setup lang="ts">
import { useUIStore } from '@/stores/ui'
const ui = useUIStore()
</script>

<style scoped>
.toast-fade-enter-active { animation: fadeInUp 0.2s ease-out; }
.toast-fade-leave-active { transition: opacity 0.3s; }
.toast-fade-leave-to { opacity: 0; }
</style>
