// ========================= 所有 WebSocket 消息的 discriminated union =========================
// 后端通过 WebSocket 下发这些消息类型，前端根据 type 字段分发处理。
// 使用 discriminated union 让 TypeScript 自动推断每个分支的字段。

// ── 历史消息中的单条记录 ──
export interface ChatMessage {
  role: 'user' | 'assistant' | 'system' | 'tool';
  content?: string;
  reasoning_content?: string;
  tool_calls?: string | ToolCall[];
  tool_call_id?: string;
  turn_id?: string;
  [key: string]: unknown;
}

export interface ToolCall {
  id?: string;
  index?: number;
  function?: {
    name: string;
    arguments: string;
  };
}

// ── Vision 图片 ──
export interface VisionImage {
  url: string;
  size: string;
}

export interface VisionChunk {
  content?: string;
}

// ── 前端发往服务端的消息 ──
export type ClientMessage =
  | { type: 'subscribe'; session_id: string }
  | { type: 'chat'; session_id: string; prompt: string }
  | { type: 'abort'; session_id: string }
  | { type: 'discard_buffer'; session_id: string }
  | { type: 'client_action_result'; request_id: string; result: unknown; tool_call_id?: string };

// ── 服务端发往前端的消息 ──
export type WsMessage =
  | { type: 'history'; messages: ChatMessage[]; streaming?: boolean }
  | { type: 'user_message'; content: string; turn_id: string }
  | { type: 'text'; content: string; tool_call_id?: string }
  | { type: 'reason'; content: string; tool_call_id?: string }
  | { type: 'tool_call_name'; tool_call_id: string; tool_call_index: number; content: string }
  | { type: 'tool_call_args'; tool_call_id: string; tool_call_index: number; content: string }
  | { type: 'tool_call_info'; tool_call_id: string; tool_call_index: number; name: string; arguments: string }
  | { type: 'tool_result'; tool_call_id: string; tool_call_index: number; content: string }
  | { type: 'stream_end' }
  | { type: 'error'; content: string }
  | { type: 'aborted' }
  | { type: 'delegate_start'; tool_call_id: string; session_uuid: string }
  | { type: 'delegate_batch_start'; tool_call_id: string; sessions: Array<{ session_uuid: string; index: number; task: string }> }
  | { type: 'vision_stream_start'; tool_call_id: string; images: VisionImage[]; question?: string; vl_high_res?: boolean }
  | { type: 'client_action_request'; request_id: string; action: string; params: Record<string, unknown>; tool_call_id?: string }
  | { type: 'client_action_resolved'; request_id: string; tool_call_id?: string }
  | { type: 'vision_images'; tool_call_id: string; images: VisionImage[] }
  | { type: 'vision_chunk'; tool_call_id: string; chunk: VisionChunk }
  | { type: 'frontend_reload' }
  | { type: 'buffer_status'; content: string }  // 刷新后恢复待发送指示器
  // 兜底：未知消息类型
  | { type: string; [key: string]: unknown };

// ── 流式 chunk（processChunk 处理的消息子集）──
export type StreamChunk =
  | { type: 'text'; content: string; tool_call_id?: string }
  | { type: 'reason'; content: string; tool_call_id?: string }
  | { type: 'tool_call_name'; tool_call_id: string; tool_call_index: number; content: string }
  | { type: 'tool_call_args'; tool_call_id: string; tool_call_index: number; content: string }
  | { type: 'tool_call_info'; tool_call_id: string; tool_call_index: number; name: string; arguments: string }
  | { type: 'tool_result'; tool_call_id: string; tool_call_index: number; content: string }
  | { type: 'vision_images'; tool_call_id: string; images: VisionImage[] }
  | { type: 'sub_task_end'; session_uuid: string };
