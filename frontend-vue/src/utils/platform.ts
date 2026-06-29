// ========================= 平台 & 路径工具 =========================
// 统一 URL 路径前缀提取逻辑，消除 api.ts / chat.ts / usePlatform.ts 三处重复。

/**
 * 从 URL 路径提取第一个路径段（不含前导斜杠）。
 * 本地开发（localhost / 127.0.0.1）返回空字符串。
 *
 * @example
 *   // URL: https://example.com/ai/chat
 *   getPathPrefix()  →  "ai"
 *
 *   // URL: http://localhost:5173/
 *   getPathPrefix()  →  ""
 */
export function getPathPrefix(): string {
  if (location.hostname === 'localhost' || location.hostname === '127.0.0.1') return '';
  const m = location.pathname.match(/^\/([^/]+)/);
  return m ? m[1] : '';
}

/**
 * 返回带前导斜杠的 URL 前缀（用于 API 路径拼接）。
 * @example getUrlPrefix() → "/ai"  或  ""
 */
export function getUrlPrefix(): string {
  const prefix = getPathPrefix();
  return prefix ? '/' + prefix : '';
}
