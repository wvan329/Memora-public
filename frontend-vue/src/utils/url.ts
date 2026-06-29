/** 解析相对路径或 data URI 为完整 URL */
export function resolveUrl(src: string): string {
  return src.startsWith('http') || src.startsWith('data:')
    ? src
    : new URL(src, window.location.href).href
}
