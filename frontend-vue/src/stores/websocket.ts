// ========================= WebSocket Store =========================
// 管理 WebSocket 连接生命周期：连接、重连、指数退避、消息事件总线。
// 外部组件/其他 store 通过 send() 发消息，通过 onMessage() 订阅。

import { ref, readonly } from 'vue';
import { defineStore } from 'pinia';
import { buildWsUrl } from '@/utils/api';
import { useAuthStore } from './auth';
import { useChatStore } from './chat';
import type { WsMessage, ClientMessage } from '@/types/ws';

type MessageListener = (msg: WsMessage) => void;

export type WsState = 'idle' | 'connecting' | 'connected' | 'reconnecting';

const MAX_RECONNECT_DELAY = 30000;

export const useWebSocketStore = defineStore('websocket', () => {
  const state = ref<WsState>('idle');
  let ws: WebSocket | null = null;
  let reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  let reconnectDelay = 1000;
  const listeners = new Set<MessageListener>();

  function connect() {
    const auth = useAuthStore();
    if (!auth.password) { state.value = 'idle'; return; }

    // 清理旧连接和重连定时器
    if (reconnectTimer) { clearTimeout(reconnectTimer); reconnectTimer = null; }
    if (ws) {
      ws.onclose = null;   // 阻止旧连接的 onclose 触发重连
      ws.close();
      ws = null;
    }

    state.value = 'connecting';
    try {
      ws = new WebSocket(buildWsUrl('chat', auth.password));
    } catch {
      scheduleReconnect();
      return;
    }

    ws.onopen = () => {
      state.value = 'connected';
      reconnectDelay = 1000;
      // 获取当前真实的 sessionId 来 subscribe
      const chat = useChatStore();
      send({ type: 'subscribe', session_id: chat.sessionId });
    };

    ws.onmessage = (event) => {
      try {
        const msg = JSON.parse(event.data) as WsMessage;
        listeners.forEach(fn => fn(msg));
      } catch { /* 忽略解析失败 */ }
    };

    ws.onclose = () => {
      state.value = 'idle';
      ws = null;
      scheduleReconnect();
    };

    ws.onerror = () => {
      // onclose 会跟随触发
    };
  }

  function disconnect() {
    if (reconnectTimer) { clearTimeout(reconnectTimer); reconnectTimer = null; }
    if (ws) {
      ws.onclose = null;   // 阻止 scheduleReconnect
      ws.close();
      ws = null;
    }
    state.value = 'idle';
    reconnectDelay = 1000;
  }

  function scheduleReconnect() {
    if (reconnectTimer) return;
    state.value = 'reconnecting';
    reconnectTimer = setTimeout(() => {
      reconnectTimer = null;
      reconnectDelay = Math.min(reconnectDelay * 2, MAX_RECONNECT_DELAY);
      connect();
    }, reconnectDelay);
  }

  function send(msg: ClientMessage) {
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify(msg));
    }
  }

  function isConnected(): boolean {
    return state.value === 'connected';
  }

  function onMessage(fn: MessageListener): () => void {
    listeners.add(fn);
    return () => { listeners.delete(fn); };
  }

  return {
    state: readonly(state),
    connect,
    disconnect,
    send,
    isConnected,
    onMessage,
  };
});
