// ========================= useVisionStreaming =========================
// 管理 vision_understand 视觉模型的流式弹窗。
//
// v4.3 重构：不再依赖 ui.startStreamingDialog。
// 直接用 ui.showDialog({ streaming: true, ... }) 创建弹窗，
// ui.updateDialog(dialogId, ...) 更新。

import { useUIStore } from '@/stores/ui';
import { useChatStore } from '@/stores/chat';
import type { StreamChunk, VisionImage } from '@/types/ws';

type RawMsg = Record<string, unknown>;

export function useVisionStreaming() {
  const ui = useUIStore();
  const chat = useChatStore();

  let _dialogId: string | null = null;
  let _text = '';
  let _imagesHtml = '';
  let _question = '';
  let _images: VisionImage[] = [];

  /** 将 VisionImage 数组转为可点击的 HTML（使用 data-url 属性供 AppDialog 代理点击） */
  function _buildImagesHtml(images: VisionImage[]): string {
    if (images.length === 0) return '';
    return images.map(img =>
      `<img src="${img.url}" alt="${img.size}" data-full-url="${img.url}" class="vision-preview-img cursor-pointer rounded-lg max-w-full" style="max-height:200px" />`
    ).join('\n');
  }

  function handleVisionStreamStart(msg: RawMsg): void {
    const toolCallId = String(msg.tool_call_id || '');
    const images = msg.images as VisionImage[] | undefined;
    _images = images || [];
    _imagesHtml = _buildImagesHtml(_images);
    _question = String(msg.question || '');

    // 构建初始 message：question（如有）+ 图片 + 加载提示
    let initialMsg = '';
    if (_question) {
      initialMsg += `<div class="text-sm text-gray-500 dark:text-gray-400 mb-2">🔍 ${_question}</div>\n\n`;
    }
    initialMsg += _imagesHtml + '\n\n⏳ 分析中…';

    _dialogId = ui.showDialog({
      message: initialMsg,
      options: [],
      confirmText: '确认',
      cancelText: '取消',
      showInput: false,
      hideActions: true,
      streaming: true,
    });

    _text = '';
    chat.setToolDialogId(toolCallId, _dialogId);
  }

  /** 构建当前弹窗的单页内容 */
  function _buildVisionPage() {
    let msg = '';
    if (_question) {
      msg += `<div class="text-sm text-gray-500 dark:text-gray-400 mb-2">🔍 ${_question}</div>\n\n`;
    }
    msg += _imagesHtml + '\n\n' + _text;
    return { message: msg, options: [] as string[], confirm_text: '确认', cancel_text: '取消' };
  }

  /**
   * 处理 vision_chunk：追加文本到识图弹窗。
   * 若无弹窗（历史重放场景），回退为 ToolCard 渲染。
   */
  function handleVisionChunk(msg: RawMsg): void {
    const chunk = (msg.chunk as Record<string, unknown> | undefined) || {};
    if (_dialogId) {
      const text = String(chunk.content || '');
      _text += text;
      const page = _buildVisionPage();
      ui.updateDialog(_dialogId, { pages: [page], message: page.message, hideActions: true });
      return;
    }
    // 回退：ToolCard 渲染（历史记录等场景）
    chat.processChunk({
      type: 'text',
      content: String(chunk.content || ''),
      tool_call_id: String(msg.tool_call_id || ''),
    } as StreamChunk);
  }

  /**
   * 处理 vision_images：更新识图弹窗中的图片。
   * 若无弹窗（历史重放场景），回退为 ToolCard 渲染。
   */
  function handleVisionImages(msg: RawMsg): void {
    if (_dialogId) {
      const images = msg.images as VisionImage[];
      if (images && images.length > 0) {
        _images = images;
        _imagesHtml = _buildImagesHtml(images);
        const page = _buildVisionPage();
        ui.updateDialog(_dialogId, { pages: [page], message: page.message, hideActions: true });
      }
      return;
    }
    // 回退：ToolCard 渲染
    chat.processChunk({
      type: 'vision_images',
      tool_call_id: String(msg.tool_call_id || ''),
      images: msg.images as VisionImage[],
    } as StreamChunk);
  }

  function reset() {
    _dialogId = null;
    _text = '';
    _imagesHtml = '';
    _question = '';
    _images = [];
  }

  /** vision 工具完成时关闭流式弹窗 */
  function finalize() {
    if (_dialogId) {
      ui.finalizeDialog(_dialogId);
      ui.closeDialog(_dialogId);
      _dialogId = null;
    }
  }

  return { handleVisionStreamStart, handleVisionChunk, handleVisionImages, finalize, reset };
}
