// ========================= UI 状态相关类型 =========================

export interface DialogPage {
  message: string;
  options?: string[];
  confirm_text?: string;
  cancel_text?: string;
}

export interface DialogState {
  message: string;                     // 提示文字（单页模式）
  options?: string[];                  // 可选项（单页模式）
  confirmText?: string;                // 确认按钮文字
  cancelText?: string;                 // 取消按钮文字
  showInput?: boolean;                 // 是否显示输入框
  inputPlaceholder?: string;           // 输入框占位文字
  inputValue?: string;                 // 输入框初始值
  requireOptions?: boolean;            // 是否强制至少选一项才能确认（默认 true）
  pages?: DialogPage[];                // 多页模式：页列表
  currentPageIndex?: number;           // 多页模式：当前页码（0-based）
  resolveOnSelect?: boolean;           // 点击选项立即提交，不等待确认按钮
  hideActions?: boolean;               // 隐藏底部确认/取消按钮
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  callback?: ((result: any) => void) | null;
}

// 确认 → { selected, text }；取消 → null
export interface DialogResult {
  selected?: string;
  text?: string;
}

/** 弹窗实例——每个弹窗独立存储，不再共用全局单例 */
export interface DialogEntry {
  id: string;
  state: DialogState;
  visible: boolean;
  resolved: boolean;
  streaming: boolean;
  /** 流式 ask_user 的回调（后端通过 WebSocket 等待响应） */
  streamingCallback: ((result: Record<string, unknown> | null) => void) | null;
}

export type ContextMenuType = 'quote' | 'image' | null;

export interface ContextMenuState {
  x: number;
  y: number;
  type: ContextMenuType;
  savedText?: string;
  imageSrc?: string;
}

export interface QuoteItem {
  id: number;
  text: string;
}
