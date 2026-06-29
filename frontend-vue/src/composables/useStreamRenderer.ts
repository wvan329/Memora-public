// ========================= useStreamRenderer =========================
// WebSocket 消息 → chat store 的桥接层。
//
// 设计决策：使用 switch-case 而非策略模式注册表。
// 原因：消息类型 20 种，新增频率极低（数月一次），switch 是最简单直接的方式。
// 旧版 stream-handler.js 也是 switch，结构保持一致便于对照维护。
//
// 消息处理不直接操作 DOM——全部通过 chat store 的 DisplayMessage[] 驱动渲染。
//
// 复杂子模块已拆分为独立 composable：
//   - useBatchDelegate.ts   批量委托弹窗管理
//   - useAskUserStreaming.ts ask_user 流式弹窗
//   - useVisionStreaming.ts 视觉流式弹窗
//   - utils/streamJsonParser.ts  增量 JSON 解析（纯函数）
//
// v4.3：各 composable 通过 dialogId 自管理弹窗，不再依赖 ui.streamingDialogId。

import { onMounted, onUnmounted } from 'vue';
import { useWebSocketStore } from '@/stores/websocket';
import { useChatStore } from '@/stores/chat';
import { useUIStore } from '@/stores/ui';
import { useSessionStore } from '@/stores/sessions';
import { useBatchDelegate } from './useBatchDelegate';
import { useAskUserStreaming } from './useAskUserStreaming';
import { useVisionStreaming } from './useVisionStreaming';
import type { WsMessage, StreamChunk } from '@/types/ws';

type RawMsg = Record<string, unknown>;

export function useStreamRenderer() {
  const wsStore = useWebSocketStore();
  const chat = useChatStore();
  const ui = useUIStore();
  const sessions = useSessionStore();

  const batchDelegate = useBatchDelegate();
  const askUserStreaming = useAskUserStreaming();
  const visionStreaming = useVisionStreaming();

  let unsubscribe: (() => void) | null = null;

  // ═══════════════════════════════════════════════════════════════
  // 消息分发
  // ═══════════════════════════════════════════════════════════════

  function handle(raw: WsMessage) {
    const msg = raw as unknown as RawMsg;
    switch (msg.type) {
      case 'history':
        if (Array.isArray(msg.messages)) {
          chat.messages = [];
          if (msg.messages.length > 0) {
            chat.loadHistory(msg.messages);
          } else {
            chat.welcomeSeed++;
          }
          if (msg.streaming) {
            chat.resumeStreamIfIncomplete();
          }
        }
        ui.setAutoScroll(true);
        return;

      case 'user_message':
        chat.handleUserMessage(String(msg.content || ''), String(msg.turn_id || ''));
        chat.markStreaming();
        chat.pendingBufferMsg = null;
        sessions.loadList();
        return;

      // 流式 chunk 统一处理
      case 'text':
      case 'reason':
      case 'sub_task_end':   // v4.5：子任务逐一完成通知，routeChunk 消费
      case 'tool_call_name':
      case 'tool_call_args':
      case 'tool_call_info':
      case 'tool_result': {
        if (chat.abortFlag) return;
        if (chat.isUserSelecting()) { chat.pauseChunk(msg as unknown as StreamChunk); return; }
        chat.flushPausedChunks();
        chat.startAIMessage();

        const c = msg as unknown as StreamChunk;

        // 批量委托：子 AI 的 text/reason chunk → 弹窗路由（消费后跳过 processChunk）
        if (batchDelegate.routeChunk(c)) return;

        // ask_user 流式弹窗（返回 dialogId 用于回写到 tool block）
        const askUserDialogId = askUserStreaming.handleChunk(c);

        // 批量委托弹窗（参数/结果处理，不消费 chunk）
        batchDelegate.handleToolChunk(c);

        // 视觉识别弹窗：tool_result 到达时关闭流式弹窗
        if (c.type === 'tool_result') visionStreaming.finalize();

        // 核心：chunk → DisplayMessage[] 数据驱动渲染
        chat.processChunk(c);

        // 将 dialogId 回写到 tool block（ToolCard 通过 dialogId 可精确恢复弹窗）
        if (askUserDialogId && 'tool_call_id' in c && c.tool_call_id) {
          chat.setToolDialogId(c.tool_call_id, askUserDialogId);
        }
        return;
      }

      case 'stream_end':
        chat.finishStreaming();
        ui.setAutoScroll(false);
        return;

      case 'error':
        ui.showToast('❌ ' + String(msg.content || ''), 3000);
        chat.finishStreaming();
        return;

      case 'aborted':
        chat.abortFlag = false;
        chat.pendingBufferMsg = null;
        chat.clearAllPending();
        chat.finishStreaming();
        return;

      case 'delegate_batch_start':
        batchDelegate.handleDelegateBatchStart(msg);
        return;

      case 'vision_stream_start':
        visionStreaming.handleVisionStreamStart(msg);
        return;

      case 'vision_chunk':
        visionStreaming.handleVisionChunk(msg);
        return;

      case 'vision_images':
        visionStreaming.handleVisionImages(msg);
        return;

      case 'frontend_reload':
        location.reload();
        return;

      case 'buffer_status':
        chat.pendingBufferMsg = String((msg as Record<string, unknown>).content || '') || null;
        return;

      default:
        return;
    }
  }

  // ═══════════════════════════════════════════════════════════════
  // 生命周期
  // ═══════════════════════════════════════════════════════════════

  onMounted(() => {
    chat.setRouteChunk((c) => batchDelegate.routeChunk(c));
    unsubscribe = wsStore.onMessage(handle);
  });

  onUnmounted(() => {
    chat.setRouteChunk(null);
    unsubscribe?.();
    batchDelegate.reset();
    askUserStreaming.reset();
    visionStreaming.reset();
  });

  return {};
}
