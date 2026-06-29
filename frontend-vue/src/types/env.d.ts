// ========================= 全局环境声明 =========================
// 供 TypeScript 识别 Android WebView 中注入的 NativeBridge 对象

declare global {
  interface Window {
    __optionDialog?: unknown;
    __confirmDialog?: unknown;
    __nativeOnMessage?: (msg: string) => void;
  }

  // Android WebView 注入的原生桥接对象
  const NativeBridge: {
    getLocation: () => void;        // 异步版：立即返回，结果通过 window.__onLocationResult 回调
    sendMessage?: (msg: string) => void;
  } | undefined;
}

export {};
