// ========================= 工具结果解析与弹窗类型映射 =========================
// 纯函数，零依赖。从 chat store 中提取以缩减 store 体积，
// 同时消除 ToolCard.vue 和 useBatchDelegate.ts 中的重复映射逻辑。

import type { ToolItemState, VisionImage, FileItem, ToolDialogType } from '@/types/chat';

/** 解析 JSON 格式的工具结果，设置 item.result / resultType / images / files / sessionUuid / batchSessions */
export function applyToolResult(item: ToolItemState, content: string) {
  item._done = true;  // tool_result 已到达，不再显示 pending
  // pending placeholder 不算真正的完成（content 会被替换）
  try { const p = JSON.parse(content); if (p?.pending === true) { item._done = false; return; } } catch { /* */ }
  try {
    const parsed = JSON.parse(content);
    if (parsed?.type === 'download') {
      item.result = JSON.stringify(parsed); item.resultType = 'download';
    } else if (parsed?.type === 'image') {
      item.result = JSON.stringify(parsed); item.resultType = 'image';
    } else if (parsed?.type === 'vision_result') {
      item.result = JSON.stringify(parsed); item.resultType = 'vision_result';
      if (!item.images || item.images.length === 0) {
        if (Array.isArray(parsed.images)) item.images = parsed.images as VisionImage[];
      }
    } else if (parsed?.type === 'files' && Array.isArray(parsed.items)) {
      item.result = JSON.stringify(parsed); item.resultType = 'files';
      item.files = parsed.items as FileItem[];
    } else if (parsed?.session_uuid) {
      item.result = parsed.result || JSON.stringify(parsed); item.resultType = 'delegate';
      item.sessionUuid = parsed.session_uuid;
    } else if (parsed?.sessions && Array.isArray(parsed.sessions)) {
      item.result = JSON.stringify(parsed); item.resultType = 'delegate';
      item.batchSessions = parsed.sessions as Array<{ session_uuid: string; index: number; task: string }>;
    } else {
      item.result = JSON.stringify(parsed, null, 2); item.resultType = 'json';
    }
  } catch {
    item.result = content || '';
    // 保留已有的 delegate/vision_result 等 resultType，不被 plain text 覆盖
    if (!item.resultType || item.resultType === 'json') {
      item.resultType = 'json';
    }
  }
}

/** 根据工具名确定弹窗类型——唯YI真相源。ToolCard 和 useBatchDelegate 统一引用此处。 */
export function getDialogType(name: string): ToolDialogType | undefined {
  if (name === 'ask_user') return 'ask_user';
  if (name === 'schedule_restart' || name === 'install_apk') return 'confirm';
  if (name === 'vision_understand') return 'vision';
  if (name === 'ai_delegate' || name === 'browser_task' || name === 'compress_context') return 'delegate';
  return undefined;
}
