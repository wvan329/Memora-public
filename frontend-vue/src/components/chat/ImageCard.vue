<template>
  <div class="inline-block mr-2 mb-2 text-center group relative">
    <!-- 图片本身：点击触发 preview 事件 -->
    <img :src="url" class="max-w-full max-h-[200px] rounded-lg block cursor-pointer" @click.stop="$emit('preview')" />
    <div class="text-[11px] text-gray-500 dark:text-gray-400 mt-0.5 flex items-center justify-center gap-1">
      <span v-if="size">{{ size }}</span>
      <!-- 保存：触发下载 -->
      <a :href="url" :download="filename || 'image'"
        @click.stop
        class="text-green-600 dark:text-green-400 hover:underline"
        title="保存图片">💾 保存</a>
    </div>
  </div>
</template>

<script setup lang="ts">
defineProps<{ url: string; filename: string; size: string }>()
defineEmits<{ preview: [] }>()
</script>
