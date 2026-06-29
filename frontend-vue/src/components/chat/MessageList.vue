<template>
  <WelcomeHint v-if="chat.messages.length === 0" />
  <template v-for="msg in chat.messages" :key="msg.id">
    <SystemMessage v-if="msg.role === 'system'" :content="msg.content" />
    <UserMessage v-else-if="msg.role === 'user'" :text="msg.content" :turn-id="msg.turnId" :is-optimistic="msg.isOptimistic" :msg-id="msg.id" />
    <AIMessage v-else-if="msg.role === 'ai'" :text="msg.content" :blocks="msg.thinkBlocks" :default-open="!!msg.isStreaming" />
  </template>
</template>

<script setup lang="ts">
import WelcomeHint from './WelcomeHint.vue'
import SystemMessage from './SystemMessage.vue'
import UserMessage from './UserMessage.vue'
import AIMessage from './AIMessage.vue'
import { useChatStore } from '@/stores/chat'
const chat = useChatStore()
</script>
