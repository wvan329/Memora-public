// ========================= 聊天 Store（核心）=========================
//
// 架构核心原则：chat.messages: DisplayMessage[] 是唯一渲染数据源。
// 流式 AI 消息和历史 AI 消息在这个数组中没有区别——只是 isStreaming 字段不同。
// chunk 到达 → processChunk() 直接 mutate 数组中最后一条消息 → Vue 响应式自动 patch DOM。
// 不存在"历史渲染"和"流式渲染"两条路径——旧版的双路径是渲染顺序 bug 的根源。
//
// thinkBlocks: ThinkBlock[] 是有序数组，保持推理块+工具卡片交错的原始顺序。
// 旧版 renderAIGroup 中 for (const msg of group) 就是交错插入的——新版通过数组顺序自然保持。
//
// onChunk 回调：每次 processChunk 后同步触发，供 ChatView 注册滚动。
// 设计为回调而非 watcher，因为 watcher 的触发时机和 Vue 批量更新之间有不可控的间隙，
// 而回调在 state mutation 的同一调用栈中触发，和旧版 scrollToBottom() 的同步调用行为一致。

import { ref } from 'vue';
import { defineStore } from 'pinia';
import { generateUUID } from '@/utils/uuid';
import { STORAGE_KEYS } from '@/utils/storageKeys';
import type { ChatMessage, StreamChunk } from '@/types/ws';
import type { ToolItemState, ToolResultType, VisionImage, FileItem, ToolDialogType } from '@/types/chat';
import { applyToolResult, getDialogType } from '@/utils/toolResult';
import { buildDisplayMessages } from '@/utils/messageBuilder';
import { getPathPrefix } from '@/utils/platform';

// ========== 会话持久化（内联，避免跨 store getter 在异步回调中 unwrap 失效）==========
/** 判断是否为移动端 WebView（Android / iOS） */
function isMobileDevice(): boolean {
  return /Android|iPhone|iPad|iPod/i.test(navigator.userAgent);
}
function loadLastSessionId(): string | null {
  if (isMobileDevice()) {
    const device = getPathPrefix();
    const key = device ? `${STORAGE_KEYS.LAST_SESSION}_${device}` : STORAGE_KEYS.LAST_SESSION;
    try { return localStorage.getItem(key); } catch { return null; }
  }
  try { const s = sessionStorage.getItem(STORAGE_KEYS.LAST_SESSION); if (s) return s; } catch { /* */ }
  try { return localStorage.getItem(STORAGE_KEYS.LAST_SESSION); } catch { return null; }
}
function saveLastSessionId(id: string) {
  if (isMobileDevice()) {
    const device = getPathPrefix();
    const key = device ? `${STORAGE_KEYS.LAST_SESSION}_${device}` : STORAGE_KEYS.LAST_SESSION;
    try { localStorage.setItem(key, id); } catch { /* */ }
  } else {
    try { sessionStorage.setItem(STORAGE_KEYS.LAST_SESSION, id); } catch { /* */ }
    try { localStorage.setItem(STORAGE_KEYS.LAST_SESSION, id); } catch { /* */ }
  }
}
function getInitialSessionId(): string {
  const params = new URLSearchParams(location.search);
  return loadLastSessionId() || params.get('s') || params.get('sub') || generateUUID();
}
function updateUrl() {
  const url = new URL(location.href);
  url.searchParams.delete('s');
  url.searchParams.delete('sub');
  history.replaceState(null, '', url.toString());
}

export interface ReasoningBlock { type: 'reasoning'; text: string }
export interface ToolBlock { type: 'tool'; key: string; item: ToolItemState }
export type ThinkBlock = ReasoningBlock | ToolBlock;

export interface DisplayMessage {
  id: string;
  role: 'system' | 'user' | 'ai';
  content: string;
  thinkBlocks: ThinkBlock[];  // 有序，保持原始交错顺序
  turnId?: string;
  isOptimistic?: boolean;
  isStreaming?: boolean;
}

export const useChatStore = defineStore('chat', () => {
  // ========== 唯一渲染数据源 ==========
  const messages = ref<DisplayMessage[]>([]);
  const sessionId = ref(getInitialSessionId());
  saveLastSessionId(sessionId.value);
  updateUrl();
  const isStreaming = ref(false);
  const abortFlag = ref(false);
  const lastUserQuestion = ref('');
  const welcomeSeed = ref(0);  // 空会话时递增触发欢迎语 AI 请求
  const pausedChunks = ref<StreamChunk[]>([]);

  // 后端忙时发送的消息不显示气泡，而是显示为底部待发送指示器
  const pendingBufferMsg = ref<string | null>(null);
  // 丢弃待发送消息后通知 InputArea 回显到输入框
  const discardedBufferMsg = ref<string | null>(null);

  // ========== onChunk 回调 ==========
  // ChatView 在 setup 期注册 doScroll，每次 state 变化后触发滚动。
  // 注意：必须在 setup 期注册（早于 App.onMounted 中的 ws.connect），
  // 否则 WebSocket 消息到达时回调还未就位，history 加载后不会滚动。
  let _onChunk: (() => void) | null = null;
  function onChunk(fn: () => void) { _onChunk = fn; }

  function msgId(): string { return 'm_' + Date.now() + '_' + Math.random().toString(36).slice(2, 8); }

  function findToolBlock(msg: DisplayMessage, key: string): ToolBlock | undefined {
    return msg.thinkBlocks.find(b => b.type === 'tool' && b.key === key) as ToolBlock | undefined;
  }

  // 工具结果解析与弹窗类型映射——统一从 toolResult.ts 引用，杜绝重复定义

  // ========== 消息操作 ==========

  function addUserMessage(text: string, optimistic = false) {
    if (optimistic) {
      // 乐观消息覆盖：如果已有未确认的乐观消息，原地替换内容（而非新增一条）
      const existing = messages.value.find(m => m.isOptimistic && m.role === 'user');
      if (existing) {
        existing.content = text;
        existing.id = msgId();  // 更新 id 以触发 Vue key 变化
        messages.value = [...messages.value];
        _onChunk?.();
        return;
      }
    }
    messages.value.push({ id: msgId(), role: 'user', content: text, thinkBlocks: [], isOptimistic: optimistic });
    _onChunk?.();
  }

  // user_message 处理：两条分支。
  // 分支 1：替换乐观消息（自己的消息被服务端确认）
  // 分支 2：turn_id 不存在 → 新增（其他客户端向此会话发送的消息）
  function handleUserMessage(content: string, turnId: string) {
    const optMsg = messages.value.find(m => m.isOptimistic && m.role === 'user');
    if (optMsg) {
      optMsg.isOptimistic = false;
      optMsg.turnId = turnId;
      _onChunk?.();
      return;
    }
    if (turnId && !messages.value.some(m => m.turnId === turnId)) {
      messages.value.push({ id: msgId(), role: 'user', content, thinkBlocks: [], turnId });
    }
    _onChunk?.();
  }

  // 如果最后一条消息是历史回放的不完整 AI 消息（resumeStreamIfIncomplete 已标记 isStreaming），
  // 复用它而非 push 新消息——避免刷新后流式分裂。
  function startAIMessage() {
    abortFlag.value = false;  // 新流式开始，清除旧 abort 标记
    const msgs = messages.value;
    const last = msgs[msgs.length - 1];
    if (last && last.role === 'ai' && last.isStreaming) {
      isStreaming.value = true;
      pausedChunks.value = [];
      return;
    }
    messages.value.push({ id: msgId(), role: 'ai', content: '', thinkBlocks: [], isStreaming: true });
    isStreaming.value = true;
    abortFlag.value = false;
    pausedChunks.value = [];
    _onChunk?.();
  }

  function getStreamingMessage(): DisplayMessage | undefined {
    const msgs = messages.value;
    for (let i = msgs.length - 1; i >= 0; i--) {
      if (msgs[i].role === 'ai' && msgs[i].isStreaming) return msgs[i];
    }
    return undefined;
  }

  function finishStreaming() {
    const msg = getStreamingMessage();
    if (msg) msg.isStreaming = false;
    isStreaming.value = false;
    abortFlag.value = false;
    pausedChunks.value = [];
    _onChunk?.();
  }

  /** 服务端确认收到用户消息后立即标记为流式状态，按钮变红。
   *  不等第一个 chunk 到达——让用户尽早看到"处理中"反馈。 */
  function markStreaming() {
    isStreaming.value = true;
  }

  // abort 后保留已渲染内容（旧版不删除 AI 消息 DOM），只清理完全空的消息
  function abortStreaming(resumeInput: string) {
    abortFlag.value = true;
    const msg = getStreamingMessage();
    if (msg) {
      msg.isStreaming = false;
      if (!msg.content && msg.thinkBlocks.length === 0) {
        const idx = messages.value.indexOf(msg);
        if (idx >= 0) messages.value.splice(idx, 1);
      }
    }
    isStreaming.value = false;
    // abortFlag 保持 true，直到服务端 aborted 或下次 startAIMessage 清除
    pausedChunks.value = [];
    lastUserQuestion.value = resumeInput;
    _onChunk?.();
  }

  // ========== Chunk 处理 ==========
  // 直接 mutate 流式消息的 content / thinkBlocks。
  // Vue 3 Proxy 深度响应式自动追踪嵌套属性变更和数组方法（push/splice），
  // 无需手动 spread 数组来触发重渲染（与 Vue 2 不同）。

  function processChunk(chunk: StreamChunk) {
    const msg = getStreamingMessage();
    if (!msg) return;

    switch (chunk.type) {
      case 'text': {
        const tcId = (chunk as Record<string, unknown>).tool_call_id as string | undefined;
        if (tcId) {
          const block = findToolBlock(msg, tcId);
          if (block) { block.item.result += (chunk.content || ''); }
        } else {
          msg.content += (chunk.content || '');
        }
        break;
      }
      case 'reason': {
        const tcId = (chunk as Record<string, unknown>).tool_call_id as string | undefined;
        if (tcId) {
          const block = findToolBlock(msg, tcId);
          if (block) { block.item.result += (chunk.content || ''); break; }
        }
        const last = msg.thinkBlocks[msg.thinkBlocks.length - 1];
        if (last && last.type === 'reasoning') last.text += (chunk.content || '');
        else msg.thinkBlocks.push({ type: 'reasoning', text: chunk.content || '' });
        break;
      }
      case 'tool_call_name': {
        const key = chunk.tool_call_id || `idx_${chunk.tool_call_index}`;
        const name = chunk.content;
        // 委托类工具立即标记为 delegate 类型，ToolCard 立即可显示「查看详情 →」。
        // 这样即使刷新重放 chunk（delegate_batch_start 是 _ephemeral 不会重放），
        // tool block 创建时就已经是 delegate 类型。
        const dt = getDialogType(name);

        // ★ 防重复：历史加载 + 快照重放 / WS 重连时 tool_call_name 可能被重复处理，
        // 若已存在同 key 的 block 则更新 name（兜底"未知工具"），不重复 push。
        const existing = findToolBlock(msg, key);
        if (existing) {
          if (!existing.item.name || existing.item.name === '未知工具') {
            existing.item.name = name;
          }
          // 同样补全 dialogType（历史回放时可能缺失）
          if (!existing.item.dialogType && dt) {
            existing.item.dialogType = dt;
          }
          break;
        }

        msg.thinkBlocks.push({ type: 'tool', key, item: { name, args: '', result: '', _done: false, resultType: dt === 'delegate' ? 'delegate' : 'json', dialogType: dt } });
        break;
      }
      case 'tool_call_args': {
        const key = chunk.tool_call_id || `idx_${chunk.tool_call_index}`;
        const block = findToolBlock(msg, key);
        if (block) {
          // 防重复：args 已是合法 JSON → 来自历史加载的完整数据，
          // chunk 快照中的增量片段无需再拼接（否则产生重复 JSON）。
          try { JSON.parse(block.item.args); break; } catch { /* 不完整 */ }
          const raw = block.item.args + chunk.content;
          try { block.item.args = JSON.stringify(JSON.parse(raw), null, 2); } catch { block.item.args = raw; }
        }
        break;
      }
      case 'tool_call_info': {
        const key = chunk.tool_call_id || `idx_${chunk.tool_call_index}`;
        let fnArgs = chunk.arguments || '';
        if (typeof fnArgs === 'string') { try { fnArgs = JSON.stringify(JSON.parse(fnArgs), null, 2); } catch { /* */ } } else { fnArgs = JSON.stringify(fnArgs, null, 2); }
        const block = findToolBlock(msg, key);
        if (block) { block.item.name = chunk.name || '未知工具'; if (fnArgs) block.item.args = fnArgs; }
        else {
          const name = chunk.name || '未知工具';
          const dt = getDialogType(name);
          msg.thinkBlocks.push({ type: 'tool', key, item: { name, args: fnArgs, result: '', resultType: dt === 'delegate' ? 'delegate' : 'json', dialogType: dt } });
        }
        break;
      }
      case 'vision_images': {
        // 流式阶段提前展示图片缩略图（不等 tool_result 到达）
        const key = chunk.tool_call_id;
        const block = findToolBlock(msg, key);
        if (block) {
          block.item.images = [...(chunk.images as VisionImage[])];
          if (!block.item.result) block.item.result = '';
          block.item.resultType = 'vision_result';
        }
        break;
      }
      case 'tool_result': {
        const key = chunk.tool_call_id || `idx_${chunk.tool_call_index}`;
        const block = findToolBlock(msg, key);
        if (!block) return;
        applyToolResult(block.item, chunk.content);
        break;
      }
    }
    _onChunk?.();
  }

  // ========== 选字暂停 ==========
  function isUserSelecting(): boolean {
    const sel = window.getSelection();
    return !!(sel && !sel.isCollapsed);
  }
  function pauseChunk(chunk: StreamChunk) { pausedChunks.value.push(chunk); }
  // 路由拦截回调：由 useStreamRenderer 注入，flushPausedChunks 重放时
  // 先过 routeChunk 再 processChunk，防止子 AI chunk 泄露到 ToolCard 结果区。
  let _routeChunk: ((c: StreamChunk) => boolean) | null = null;
  function setRouteChunk(fn: ((c: StreamChunk) => boolean) | null) { _routeChunk = fn; }

  function flushPausedChunks() {
    const queued = [...pausedChunks.value];
    pausedChunks.value = [];
    queued.forEach(c => {
      if (_routeChunk?.(c)) return;
      processChunk(c);
    });
  }

  // ========== 提前标记 delegate 类型 ==========
  // delegate_batch_start 到达时立即标记 tool block 为 delegate，
  // 这样 ToolCard 的「查看详情 →」不用等所有子 AI 执行完才出现。
  function markToolDelegate(toolCallId: string) {
    const msg = getStreamingMessage();
    if (!msg) return;
    const block = findToolBlock(msg, toolCallId);
    if (block) {
      block.item.resultType = 'delegate';
      _onChunk?.();
    }
  }

  // ========== ask_user / delegate 弹窗关联 ==========
  // 将 dialogId 回写到指定的 tool block，ToolCard 点击时可通过 item.dialogId 精确恢复
  function setToolDialogId(toolCallId: string, dialogId: string) {
    for (const msg of messages.value) {
      if (msg.role !== 'ai') continue;
      const block = findToolBlock(msg, toolCallId);
      if (block) { block.item.dialogId = dialogId; return; }
    }
  }

  function getToolDialogId(toolCallId: string): string | undefined {
    for (const msg of messages.value) {
      if (msg.role !== 'ai') continue;
      const block = findToolBlock(msg, toolCallId);
      if (block) return block.item.dialogId;
    }
    return undefined;
  }

  // ========== 历史消息载入 ==========
  function loadHistory(msgs: ChatMessage[]) { messages.value = buildDisplayMessages(msgs); }

  // abort 兜底：强制清除所有 pending 状态的 ToolBlock，确保 🔄 不残留
  function clearAllPending() {
    for (const msg of messages.value) {
      if (msg.role !== 'ai') continue;
      for (const block of msg.thinkBlocks) {
        if (block.type === 'tool' && !block.item._done) {
          block.item._done = true;
          if (!block.item.result) block.item.result = '已终止';
        }
      }
    }
  }

  // 历史加载后检查最后一条消息是否为不完整 AI 消息。
  // 仅标记 isStreaming，不改变全局 isStreaming 状态。
  //
  // v4.5 修复：原只判断 !last.content && last.thinkBlocks.length > 0，
  // 漏掉了「已输出文本 + 工具调用后 AI 继续思考/输出」等场景。
  //
  // 现在由后端 streaming 信号（has_task）提供权威判断：只要会话有活跃任务，
  // 最后一条 AI 消息必然属于当前未完成的 turn，直接标记 isStreaming。
  // 即使极端情况下标记了已完成的旧消息（release 延迟），也无害——
  // 因为新 user 消息 push 后 last 变成 user，startAIMessage 会创建新消息而非复用。
  function resumeStreamIfIncomplete() {
    const msgs = messages.value;
    if (msgs.length === 0) return;
    const last = msgs[msgs.length - 1];
    if (last.role === 'ai') {
      last.isStreaming = true;
      isStreaming.value = true;  // 全局标记 → 发送按钮变红
    }
  }

  // buildDisplayMessages / buildOneAI → 见 @/utils/messageBuilder.ts

  // 直接清空——不经过 finishStreaming，避免空消息闪现"思考中…"
  function newSession() {
    isStreaming.value = false;
    abortFlag.value = false;
    pausedChunks.value = [];
    pendingBufferMsg.value = null;
    messages.value = [];
    sessionId.value = generateUUID();
    saveLastSessionId(sessionId.value);
    updateUrl();
  }

  /** 切换到指定会话，同时持久化到 localStorage 以便下次打开时恢复 */
  function switchSession(id: string) {
    if (id === sessionId.value) return;
    finishStreaming();
    pendingBufferMsg.value = null;
    pausedChunks.value = [];
    sessionId.value = id;
    messages.value = [];
    welcomeSeed.value = 0;
    saveLastSessionId(id);
    updateUrl();
  }

  return {
    messages, sessionId, isStreaming, abortFlag, lastUserQuestion, welcomeSeed, pausedChunks, pendingBufferMsg, discardedBufferMsg,
    addUserMessage, handleUserMessage, startAIMessage, getStreamingMessage,
    finishStreaming, markStreaming, abortStreaming, processChunk,
    isUserSelecting, pauseChunk, flushPausedChunks, setRouteChunk,
    markToolDelegate,
    setToolDialogId,
    getToolDialogId,
    clearAllPending, loadHistory, resumeStreamIfIncomplete, newSession, switchSession,
    onChunk,
  };
});
