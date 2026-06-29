// ========================= useClientAction =========================
//
// v4.3 重构：ask_user 分支不再依赖 ui.streamingDialogId。
// 通过 toolCallId → chat.getToolDialogId 查找流式弹窗。

import { useWebSocketStore } from '@/stores/websocket';
import { useUIStore } from '@/stores/ui';
import { useChatStore } from '@/stores/chat';
import { openFilePicker, uploadImages } from '@/utils/vision';
import type { WsMessage } from '@/types/ws';
import type { DialogState } from '@/types/ui';

export function useClientAction() {
  const wsStore = useWebSocketStore();
  const ui = useUIStore();
  let unsubscribe: (() => void) | null = null;

  function setup() {
    const chat = useChatStore();

    unsubscribe = wsStore.onMessage((msg: WsMessage) => {
      // 弹窗已关闭通知：任意窗口处理后，广播到所有窗口关闭对应弹窗
      if (msg.type === 'client_action_resolved') {
        const resolved = msg as { request_id: string; tool_call_id?: string };
        const tcId = resolved.tool_call_id || '';
        if (tcId) {
          const dialogId = chat.getToolDialogId(tcId);
          if (dialogId) {
            ui.closeDialog(dialogId);
            // 清理 tool block 中的 dialogId，防止 ToolCard 误恢复已处理的弹窗
            chat.setToolDialogId(tcId, '');
          }
        }
        return;
      }

      if (msg.type !== 'client_action_request') return;

      const req = msg as { request_id: string; action: string; params: Record<string, unknown>; tool_call_id?: string };
      const { request_id, action, params } = req;
      const toolCallId = req.tool_call_id || '';

      const respond = (result: unknown) => {
        wsStore.send({
          type: 'client_action_result',
          request_id: String(request_id),
          result,
          tool_call_id: toolCallId || undefined,
        });
      };

      switch (action) {
        case 'get_location':
          handleGetLocation(respond);
          break;

        case 'confirm': {
          const dialogId = showDialog(
            (params?.message as string) || '',
            undefined,
            (params?.confirm_text as string) || '确认',
            (params?.cancel_text as string) || '取消',
            false,
            (result) => respond(result ? { confirmed: true } : { cancelled: true })
          );
          if (toolCallId && dialogId) chat.setToolDialogId(toolCallId, dialogId);
          break;
        }

        case 'ask_user': {
          const rawPages = (params?.pages as Array<Record<string, unknown>>) || [];
          const pages = rawPages.map(p => ({
            message: (p.message as string) || '',
            options: (p.options as string[]) || [],
            confirm_text: (p.confirm_text as string) || '确认',
            cancel_text: (p.cancel_text as string) || '取消',
          }));

          // 查找流式弹窗：通过 toolCallId 从 tool block 获取 dialogId
          const streamingDlgId = toolCallId ? chat.getToolDialogId(toolCallId) : undefined;

          if (streamingDlgId) {
            // 流式弹窗已存在 → finalize + 设置 callback
            const entry = ui.dialogs.find(e => e.id === streamingDlgId);
            const keepPage = entry?.state.currentPageIndex;
            ui.setStreamingCallback(streamingDlgId, (result) => respond(result || { cancelled: true }));
            ui.finalizeDialog(streamingDlgId);
            ui.updateDialog(streamingDlgId, {
              pages,
              currentPageIndex: (keepPage !== undefined && keepPage < pages.length) ? keepPage : 0,
            });
          } else {
            // 无流式弹窗（参数一次性到达）→ 直接创建
            const dialogId = showDialog({
              message: '',
              options: [],
              confirmText: '确认',
              cancelText: '取消',
              showInput: true,
              pages,
              currentPageIndex: 0,
              callback: (result) => respond(result || { cancelled: true }),
            });
            if (toolCallId && dialogId) chat.setToolDialogId(toolCallId, dialogId);
          }
          break;
        }

        case 'pick_images':
          pickImages(params, respond);
          break;

        case 'pick_images_with_options':
          pickImagesWithOptions(params, respond, toolCallId);
          break;

        default:
          respond({ error: '不支持的客户端操作: ' + action });
      }
    });
  }

  function handleGetLocation(respond: (result: unknown) => void) {
    if (typeof NativeBridge !== 'undefined') {
      try {
        // 异步回调模式：getLocation() 立即返回（不阻塞），
        // 定位完成后 Kotlin 端通过 evaluateJavascript 调用 window.__onLocationResult(json)
        const nb = NativeBridge as { getLocation: () => void };
        (window as unknown as Record<string, unknown>).__onLocationResult = (json: string) => {
          try {
            // __location_result__ 前缀由 MainActivity pushToJs 拼接，
            // 此处做兼容处理：有前缀就去掉，没有就直接 parse
            const raw = json.startsWith('__location_result__') ? json.slice(20) : json;
            const loc = JSON.parse(raw);
            if (loc.error) { respond({ error: loc.error }); }
            else { respond({ lat: loc.lat, lng: loc.lng, accuracy: loc.accuracy, type: loc.type }); }
          } catch (e) { respond({ error: '定位解析失败: ' + (e as Error).message }); }
          delete (window as unknown as Record<string, unknown>).__onLocationResult;
        };
        nb.getLocation();
      } catch (e) { respond({ error: '获取位置失败: ' + (e as Error).message }); }
    } else if (navigator.geolocation) {
      navigator.geolocation.getCurrentPosition(
        (pos) => respond({ lat: pos.coords.latitude, lng: pos.coords.longitude, accuracy: pos.coords.accuracy }),
        (err) => respond({ error: '浏览器定位失败: ' + err.message }),
        { timeout: 8000, enableHighAccuracy: true }
      );
    } else {
      respond({ error: '此浏览器不支持定位' });
    }
  }

  function showDialog(
    messageOrState: string | DialogState,
    options?: string[],
    confirmText?: string,
    cancelText?: string,
    showInput?: boolean,
    callback?: (result: Record<string, unknown> | null) => void
  ): string {
    if (typeof messageOrState === 'string') {
      return ui.showDialog({
        message: messageOrState,
        options: options || [],
        confirmText: confirmText || '确认',
        cancelText: cancelText || '取消',
        showInput: showInput !== false,
        callback: callback || null,
      });
    } else {
      return ui.showDialog(messageOrState);
    }
  }

  async function pickImages(opts: Record<string, unknown>, respond: (result: unknown) => void) {
    const files = await openFilePicker();
    if (!files || files.length === 0) { respond({ error: '未选择图片' }); return; }
    try {
      const urls = await uploadImages(files, 1000, 0.5);
      respond({ urls });
    } catch (e) { respond({ error: '选图上传失败: ' + (e as Error).message }); }
  }

  async function pickImagesWithOptions(opts: Record<string, unknown>, respond: (result: unknown) => void, _toolCallId?: string) {
    const files = await openFilePicker();

    if (!files || files.length === 0) {
      respond({ error: '未选择图片' });
      return;
    }

    const question = (opts.default_question as string) || '图中描绘的是什么？';
    const thinking = false;
    const vl_high_res = useUIStore().visionHighRes;

    const maxSize = vl_high_res ? 2000 : 1300;
    const quality = vl_high_res ? 0.8 : 0.6;

    try {
      const urls = await uploadImages(files, maxSize, quality);
      respond({ urls, question, thinking, vl_high_res });
    } catch (e) {
      respond({ error: '选图上传失败: ' + (e as Error).message });
    }
  }

  return { setup, unsubscribe };
}
