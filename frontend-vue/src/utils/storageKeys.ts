// ========================= 存储键名常量 =========================
// 所有 localStorage / sessionStorage key 集中定义，避免散落硬编码。

export const STORAGE_KEYS = {
  AUTH_PASSWORD: 'ai_access_password',
  LAST_SESSION: 'ai_last_session',
  SIDEBAR_COLLAPSED: 'memora_sidebar_collapsed',
  THINK_TOOLS_OPEN: 'memora_think_tools_open',
  CONTENT_EXPANDED: 'memora_content_expanded',
  ALLOW_TOGGLE: 'memora_allow_toggle',
  FONT_SIZE: 'memora_font_size',
  LAST_SENT: 'memora_last_sent',
} as const;
