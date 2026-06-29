// ========================= 聊天相关的类型 =========================
import type { ChatMessage as WsChatMessage } from './ws';
export type ChatMessage = WsChatMessage;

export interface SessionSummary {
  user_id: string;
  title: string;
  last_time: string;
  pinned: boolean;
  custom_title: string | null;
  display_title?: string;  // 由后端计算：custom_title || title || '(空会话)'
}

/** ToolCard 弹窗类型——替代模板中硬编码的工具名判断 */
export type ToolDialogType = 'ask_user' | 'confirm' | 'vision' | 'delegate';

export interface ToolItemState {
  name: string;
  args: string;
  result: string;
  resultType: ToolResultType;
  _done?: boolean;  // tool_result 已到达（即使 content 为空），用于区分 pending 和空结果
  sessionUuid?: string;  // delegate 工具的子会话 UUID（单任务模式）
  batchSessions?: Array<{ session_uuid: string; index: number; task: string }>;  // delegate 批量模式：所有子会话信息
  images?: VisionImage[];  // vision 工具：流式阶段提前展示缩略图
  files?: FileItem[];      // download/files 工具：多文件列表
  dialogId?: string;       // ask_user / batch delegate 弹窗 ID（用于历史 ToolCard 精确恢复）
  dialogType?: ToolDialogType;  // 弹窗类型——ToolCard 据此渲染「打开弹窗 →」，不再硬编码工具名
}

export interface VisionImage {
  url: string;
  size: string;
}

export interface FileItem {
  type: 'image' | 'download' | 'error';
  url: string;
  filename: string;
  size?: number;
  error?: string;
}

export type ToolResultType =
  | 'json'
  | 'download'
  | 'image'
  | 'vision_result'
  | 'delegate'
  | 'files';
