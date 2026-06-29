// ========================= Markdown 渲染与工具调用解析 =========================
import { marked } from 'marked';
import type { ToolCall } from '@/types/ws';

// marked 一次性初始化
marked.use({ breaks: true, gfm: true });

// 纯文本转义（防 XSS）
export function escapeHtml(str: string): string {
  const div = document.createElement('div');
  div.textContent = str;
  return div.innerHTML;
}

// 时间格式化
export function formatTime(isoStr: string): string {
  if (!isoStr) return '';
  try {
    const d = new Date(isoStr.replace(' ', 'T') + 'Z');
    if (isNaN(d.getTime())) return '';
    return d.toLocaleString('zh-CN', {
      month: 'numeric', day: 'numeric',
      hour: '2-digit', minute: '2-digit',
    });
  } catch { return ''; }
}

// Markdown 渲染（相对路径转绝对 URL + 注入认证 password 到图片和下载链接）
export function renderMarkdown(text: string, password?: string): string {
  if (!text) return '';
  try {
    // 不再插入零宽空格来「帮助」marked 识别分隔符。
    // 零宽空格 \u200B 属于 \p{Cf}（格式字符），marked 不将其视为空白或标点，
    // 反而会破坏 ** / __ 的 flanking 条件，导致粗体/斜体渲染失败。
    // 现代 marked（gfm: true）使用 Unicode \p{P} 正确识别 CJK 标点，
    // 无需额外预处理。
    // 保护代码块/行内代码中的 ~ 和 \（marked 会错误转义 \. \* 等组合，导致 Windows 路径损坏）
    text = text.replace(/(```[\s\S]*?```|`[^`]+`)/g, (m) => m.replace(/~/g, '\u0000T\u0000').replace(/\\/g, '\u0000BS\u0000'));
    text = text.replace(/~~/g, '\u0000DL\u0000');
    text = text.replace(/~/g, '\\~');
    text = text.replace(/\u0000DL\u0000/g, '~~');
    text = text.replace(/\u0000T\u0000/g, '~');
    // 非代码块的反斜杠 → 正斜杠（根治 marked 把 \. \* 等转义导致 Windows 路径损坏）
    text = text.replace(/\\/g, '/');
    text = text.replace(/\u0000BS\u0000/g, '\\');
    let html = marked.parse(text) as string;

    // 通用 URL 处理：本地路径自动转 api/download；公网 URL 直连
    const injectPassword = (rawUrl: string): string => {
      let url = rawUrl;

      // 公网 URL 或 data URL → 原样
      if (rawUrl.startsWith('http') || rawUrl.startsWith('data:')) {
        return url;
      }

      // 已含 /api/download → 补全绝对 URL + 注入密码
      if (url.includes('/api/download')) {
        if (!url.startsWith('http')) {
          url = new URL(url, window.location.href).href;
        }
        if (password && !url.includes('password=')) {
          const sep = url.includes('?') ? '&' : '?';
          url = url + sep + 'password=' + encodeURIComponent(password);
        }
        return url;
      }

      // 本地路径 → 自动代理为 api/download
      // 注意：不能用 /api/download（绝对路径会丢失 /home/ 前缀），
      // 必须用 api/download 相对路径，浏览器基于当前页面 URL 补全
      url = 'api/download?path=' + encodeURIComponent(rawUrl);
      if (password) {
        url = url + '&password=' + encodeURIComponent(password);
      }
      return url;
    };

    // <img> 标签图片
    html = html.replace(/<img\s[^>]*src="([^"]+)"/g, (_match: string, src: string) => {
      const newSrc = injectPassword(src);
      return newSrc !== src ? _match.replace(src, newSrc) : _match;
    });
    // <a> 标签下载链接
    html = html.replace(/<a\s[^>]*href="([^"]+)"/g, (_match: string, href: string) => {
      const newHref = injectPassword(href);
      return newHref !== href ? _match.replace(href, newHref) : _match;
    });
    return html;
  } catch { return escapeHtml(text); }
}

// 工具调用解析（兼容字符串和数组格式）
export function parseToolCalls(toolCalls: string | ToolCall[] | undefined | null): ToolCall[] | null {
  if (!toolCalls) return null;
  if (typeof toolCalls === 'string') {
    try { return JSON.parse(toolCalls); } catch { return null; }
  }
  if (Array.isArray(toolCalls)) return toolCalls;
  return null;
}
