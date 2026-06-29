// ========================= useAskUserStreaming =========================
// 管理 ask_user 工具调用的流式弹窗——在参数完整到达前逐 chunk 填充弹窗内容。
//
// v4.3 重构：不再依赖 ui.streamingDialogId 全局单例。
// 直接用 ui.showDialog({ streaming: true, ... }) 创建弹窗，
// ui.updateDialog(dialogId, ...) 更新，dialogId 自管理。

import { useUIStore } from '@/stores/ui';
import { extractPagesFromPartial, extractPartialMessageFromLastPage } from '@/utils/streamJsonParser';
import type { StreamChunk } from '@/types/ws';

export function useAskUserStreaming() {
  const ui = useUIStore();

  let _dialogId: string | null = null;
  let _argsBuffer = '';

  /** 返回当前流式弹窗的 dialogId，供调用方通过 setToolDialogId 回写到 tool block */
  function handleChunk(c: StreamChunk): string | null {
    if (c.type === 'tool_call_name' && c.content === 'ask_user') {
      _argsBuffer = '';
      _dialogId = ui.showDialog({
        message: '⏳ AI 正在准备问题…',
        options: [],
        confirmText: '确认',
        cancelText: '取消',
        showInput: true,
        pages: [],
        currentPageIndex: 0,
        streaming: true,
      });
      return _dialogId;
    }

    if (!_dialogId) return null;

    if (c.type === 'tool_call_args') {
      tryParseArgs(c.content || '');
    } else if (c.type === 'tool_call_info') {
      tryParseArgs(JSON.stringify(c.arguments || {}));
    }

    return null;
  }

  function tryParseArgs(chunk: string) {
    _argsBuffer += chunk;
    try {
      const parsed = JSON.parse(_argsBuffer);
      const rawPages = (parsed as Record<string, unknown>).pages as Array<Record<string, unknown>> | undefined;
      if (rawPages && rawPages.length > 0) {
        const pages = rawPages.map(p => ({
          message: (p.message as string) || '',
          options: (p.options as string[]) || [],
          confirm_text: (p.confirm_text as string) || '确认',
          cancel_text: (p.cancel_text as string) || '取消',
        }));
        ui.updateDialog(_dialogId!, { pages, message: pages[0]?.message || '' });
      }
    } catch {
      const pagesArr = extractPagesFromPartial(_argsBuffer);
      const partialMsg = extractPartialMessageFromLastPage(_argsBuffer);

      if (pagesArr.length > 0) {
        const pages = pagesArr.map(p => ({
          message: (p.message as string) || '',
          options: (p.options as string[]) || [],
          confirm_text: (p.confirm_text as string) || '确认',
          cancel_text: (p.cancel_text as string) || '取消',
        }));
        if (partialMsg !== null && partialMsg.length > 0) {
          pages.push({ message: partialMsg, options: [], confirm_text: '确认', cancel_text: '取消' });
        }
        ui.updateDialog(_dialogId!, { pages, message: pages[0]?.message || '' });
      } else if (partialMsg !== null && partialMsg.length > 0) {
        ui.updateDialog(_dialogId!, {
          pages: [{ message: partialMsg, options: [], confirm_text: '确认', cancel_text: '取消' }],
          message: partialMsg,
        });
      }
    }
  }

  function reset() {
    _dialogId = null;
    _argsBuffer = '';
  }

  return { handleChunk, reset };
}
