// ========================= 共用格式化函数 =========================

/** 文件大小格式化，返回 " (1.5 MB)" 格式（前置空格+括号，用于链接文本拼接） */
export function formatFileSize(bytes: number): string {
  if (!bytes) return ''
  const units = ['B', 'KB', 'MB', 'GB']
  let i = 0
  let size = bytes
  while (size >= 1024 && i < units.length - 1) { size /= 1024; i++ }
  return ` (${size.toFixed(i === 0 ? 0 : 1)} ${units[i]})`
}
