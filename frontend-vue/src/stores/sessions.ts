// ========================= 会话列表 Store =========================
// 管理侧边栏会话列表的加载、删除、置顶、重命名。
// 置顶状态和自定义标题存储在服务端 session_meta 表，所有设备共享。
// 分页：首次加载 50 条非置顶，滚动到底部加载更多；置顶会话始终全部展示。

import { ref, computed } from 'vue';
import { defineStore } from 'pinia';
import { apiPath, authHeaders } from '@/utils/api';
import { useAuthStore } from './auth';
import { useChatStore } from './chat';
import { useWebSocketStore } from './websocket';
import type { SessionSummary } from '@/types/chat';

const PAGE_SIZE = 50;

export const useSessionStore = defineStore('session', () => {
  // 置顶会话（全部，不分页）
  const pinnedSessions = ref<SessionSummary[]>([]);
  // 非置顶会话（分页累积）
  const unpinnedSessions = ref<SessionSummary[]>([]);
  const loading = ref(false);
  const error = ref('');
  const hasMore = ref(false);
  const offset = ref(0);

  // 合并后用于排序显示：置顶在前，非置顶在后
  const sortedSessions = computed(() => {
    return [...pinnedSessions.value, ...unpinnedSessions.value];
  });

  function isPinned(id: string): boolean {
    return pinnedSessions.value.some(s => s.user_id === id);
  }

  /** 首次加载 / 刷新：重置 offset，清空列表 */
  async function loadList() {
    const auth = useAuthStore();
    if (!auth.password) return;
    loading.value = true;
    error.value = '';
    offset.value = 0;
    try {
      const resp = await fetch(apiPath(`/sessions?offset=0&limit=${PAGE_SIZE}`), {
        headers: authHeaders(auth.password),
      });
      if (resp.status === 401) { auth.handle401(); return; }
      if (!resp.ok) throw new Error('加载失败');
      const data = await resp.json();
      pinnedSessions.value = data.pinned || [];
      unpinnedSessions.value = data.sessions || [];
      hasMore.value = data.has_more === true;
      offset.value = (data.sessions || []).length;
    } catch {
      error.value = '加载失败';
      pinnedSessions.value = [];
      unpinnedSessions.value = [];
      hasMore.value = false;
    } finally {
      loading.value = false;
    }
  }

  /** 滚动加载更多非置顶会话 */
  async function loadMore() {
    if (loading.value || !hasMore.value) return;
    const auth = useAuthStore();
    if (!auth.password) return;
    loading.value = true;
    try {
      const resp = await fetch(apiPath(`/sessions?offset=${offset.value}&limit=${PAGE_SIZE}`), {
        headers: authHeaders(auth.password),
      });
      if (!resp.ok) throw new Error('加载失败');
      const data = await resp.json();
      // 追加到现有列表
      unpinnedSessions.value = [...unpinnedSessions.value, ...(data.sessions || [])];
      hasMore.value = data.has_more === true;
      offset.value += (data.sessions || []).length;
    } catch {
      // 加载更多失败静默处理，保留已有数据
    } finally {
      loading.value = false;
    }
  }

  /** 切换置顶状态 → 服务端持久化 → 刷新列表 */
  async function togglePin(id: string) {
    const auth = useAuthStore();
    const currentlyPinned = isPinned(id);
    try {
      const resp = await fetch(apiPath(`/sessions/${encodeURIComponent(id)}/pin`), {
        method: 'PUT',
        headers: {
          ...authHeaders(auth.password),
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({ pinned: !currentlyPinned }),
      });
      if (!resp.ok) throw new Error('操作失败');
      // 刷新列表以反映置顶变化（会话可能在 pinned/unpinned 之间移动）
      await loadList();
    } catch { /* 静默 */ }
  }

  /** 重命名会话 → PUT /sessions/{id}/rename → 刷新列表 */
  async function renameSession(id: string, title: string) {
    const auth = useAuthStore();
    try {
      const resp = await fetch(apiPath(`/sessions/${encodeURIComponent(id)}/rename`), {
        method: 'PUT',
        headers: {
          ...authHeaders(auth.password),
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({ title }),
      });
      if (!resp.ok) throw new Error('操作失败');
      // 局部更新避免整表刷新——更新 pinned 和 unpinned 中匹配的条目
      const updateTitle = (arr: SessionSummary[]) => {
        const s = arr.find(s => s.user_id === id);
        if (s) {
          s.custom_title = title || null;
          s.display_title = title || s.title || '(空会话)';
        }
      };
      updateTitle(pinnedSessions.value);
      updateTitle(unpinnedSessions.value);
    } catch { /* 静默 */ }
  }

  async function deleteSession(id: string) {
    const auth = useAuthStore();
    const chat = useChatStore();
    try {
      await fetch(apiPath(`/sessions/${encodeURIComponent(id)}`), {
        method: 'DELETE',
        headers: authHeaders(auth.password),
      });
      if (id === chat.sessionId) chat.newSession();
      // 局部移除，无需刷新整表
      pinnedSessions.value = pinnedSessions.value.filter(s => s.user_id !== id);
      unpinnedSessions.value = unpinnedSessions.value.filter(s => s.user_id !== id);
    } catch { /* 静默 */ }
  }

  // 注意：确认弹窗在调用方（ContextMenu.handleDelete）处理，
  // 这里不重复弹 confirm，避免双重确认。
  async function deleteTurn(turnId: string) {
    const auth = useAuthStore();
    const chat = useChatStore();
    const ws = useWebSocketStore();
    if (chat.isStreaming) {
      ws.send({ type: 'abort', session_id: chat.sessionId });
      chat.finishStreaming();
    }
    try {
      await fetch(apiPath(`/turns/${encodeURIComponent(chat.sessionId)}/${encodeURIComponent(turnId)}`), {
        method: 'DELETE',
        headers: authHeaders(auth.password),
      });
    } catch { /* 静默 */ }
    // 删除了消息 → 清空重载当前会话。不刷侧边栏（标题和时间未变）
    chat.messages = [];
    if (ws.isConnected()) {
      ws.send({ type: 'subscribe', session_id: chat.sessionId });
    }
  }

  return {
    pinnedSessions, unpinnedSessions, sortedSessions, loading, error, hasMore,
    isPinned, loadList, loadMore, togglePin, renameSession,
    deleteSession, deleteTurn,
  };
});