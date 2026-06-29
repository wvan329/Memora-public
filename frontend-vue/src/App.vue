<template>
  <div class="bg-gray-50 dark:bg-gray-950 flex flex-row h-full">
    <LoginOverlay />
    <AppSidebar />
    <ChatView />
    <ContextMenu />
    <AppDialog v-for="entry in ui.dialogs" :key="entry.id" :dialog-id="entry.id" />
    <AppToast />
  </div>
</template>

<script setup lang="ts">
import { onMounted } from 'vue'
import LoginOverlay from '@/components/overlay/LoginOverlay.vue'
import AppSidebar from '@/components/sidebar/AppSidebar.vue'
import ChatView from '@/components/chat/ChatView.vue'
import ContextMenu from '@/components/context-menu/ContextMenu.vue'
import AppDialog from '@/components/overlay/AppDialog.vue'
import AppToast from '@/components/common/AppToast.vue'
import { useAuthStore } from '@/stores/auth'
import { useWebSocketStore } from '@/stores/websocket'
import { useSessionStore } from '@/stores/sessions'
import { useUIStore } from '@/stores/ui'
import { useStreamRenderer } from '@/composables/useStreamRenderer'
import { useClientAction } from '@/composables/useClientAction'

const auth = useAuthStore()
const wsStore = useWebSocketStore()
const sessions = useSessionStore()
const ui = useUIStore()

useStreamRenderer()
const clientAction = useClientAction()

onMounted(() => {
  // 恢复持久化字号
  ui.setFontSize(ui.fontSize)
  clientAction.setup()
  if (auth.password) {
    wsStore.connect()
    sessions.loadList()
  }
})
</script>
