// ========================= API URL 构建 =========================
import { STORAGE_KEYS } from './storageKeys';
import { getUrlPrefix } from './platform';

export function apiPath(path: string): string {
  return getUrlPrefix() + path;
}

// WebSocket URL：根据当前页面协议自动选择 ws/wss
export function buildWsUrl(connType: string, password: string): string {
  const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
  const prefix = getUrlPrefix();
  return `${protocol}//${location.host}${prefix}/ws?password=${encodeURIComponent(password)}&conn=${connType}`;
}

// HTTP 请求认证头
export function authHeaders(password: string, extra: Record<string, string> = {}): Record<string, string> {
  const h = { ...extra };
  if (password) h['X-Access-Password'] = password;
  return h;
}

// 401 处理：清空密码
export function handleAuthError(status: number): boolean {
  if (status === 401) {
    localStorage.removeItem(STORAGE_KEYS.AUTH_PASSWORD);
    return true;
  }
  return false;
}
