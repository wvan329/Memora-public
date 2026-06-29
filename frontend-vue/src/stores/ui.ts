// ========================= UI Store =========================
// 弹窗管理：每个弹窗独立实例，通过 dialogId 区分。
// 不再使用全局单例——多个弹窗可以同时存在，互不影响。
//
// 两种弹窗类型：
//   - 本地弹窗（删除确认等）：调用 showDialog() 获得 dialogId，callback 中 closeDialog(id)
//   - 后端弹窗（ask_user/confirm）：由各 composable 自行创建和管理 dialogId，
//     不再通过全局 streamingDialogId 间接操作。
//
// v4.3 重构：
//   - 废弃 streamingDialogId / startStreamingDialog / updateStreamingDialog / finalizeStreamingDialog
//   - 流式弹窗直接用 showDialog({ streaming: true, ... }) 创建，updateDialog(id, ...) 更新
//   - 保留 setStreamingCallback 供 useClientAction 使用

import { ref, reactive, computed } from 'vue';
import { defineStore } from 'pinia';
import type { ContextMenuState, DialogState, DialogEntry } from '@/types/ui';
import { STORAGE_KEYS } from '@/utils/storageKeys';

export const useUIStore = defineStore('ui', () => {
  const saved = localStorage.getItem(STORAGE_KEYS.SIDEBAR_COLLAPSED);
  const sidebarCollapsed = ref(saved === 'true');
  const autoScroll = ref(false);

  // 思考与工具调用区域默认展开（持久化）
  const thinkToolsOpen = ref(localStorage.getItem(STORAGE_KEYS.THINK_TOOLS_OPEN) === 'true');
  // 内容框（推理块/工具卡片/用户消息）默认展开（持久化）
  const contentExpanded = ref(localStorage.getItem(STORAGE_KEYS.CONTENT_EXPANDED) === 'true');
  // 允许手动点击展开/折叠（持久化，默认开启）
  const allowToggle = ref(localStorage.getItem(STORAGE_KEYS.ALLOW_TOGGLE) !== 'false');
  // 全局字号（持久化，默认 16px，范围 12~20）
  const fontSize = ref(Number(localStorage.getItem(STORAGE_KEYS.FONT_SIZE)) || 16);
  // 手机通知开关（后端持久化，前端读取）
  const notificationEnabled = ref(false);
  // 高精度图片识别开关（后端持久化，前端读取）
  const visionHighRes = ref(false);
  // 有头浏览器开关（后端持久化，前端读取）
  const browserHeaded = ref(false);
  const programScrolling = ref(false);
  // 输入框聚焦请求计数器：外部递增，InputArea 监听变化后 focus
  const focusInputRequest = ref(0);
  const isComposing = ref(false);  // 输入法激活中，点击空白不触发欢迎语刷新
  const toastMessage = ref('');
  const toastVisible = ref(false);
  const contextMenu = ref<ContextMenuState | null>(null);

  // ── 弹窗实例列表 ──
  const dialogs = ref<DialogEntry[]>([]);
  // 最后一个被最小化的弹窗 ID（ToolCard 恢复用）
  const lastMinimizedId = ref<string | null>(null);

  let toastTimer: ReturnType<typeof setTimeout> | null = null;

  function _genId(): string {
    return 'dlg_' + Date.now() + '_' + Math.random().toString(36).slice(2, 6);
  }

  /** 创建 DialogEntry 的默认 state */
  function _defaultState(): DialogState {
    return {
      message: '', options: [], confirmText: '确认', cancelText: '取消',
      showInput: true, pages: [], currentPageIndex: 0, callback: null,
    };
  }

  // ═══════════════════════════════════════════════════════════════
  // 弹窗 API
  // ═══════════════════════════════════════════════════════════════

  /** 打开一个弹窗，返回 dialogId。
   *
   * 可传入 streaming: true 创建流式弹窗（如 ask_user / delegate / vision），
   * 后续通过 updateDialog(id, ...) 更新内容。
   */
  function showDialog(state: DialogState & { streaming?: boolean }): string {
    const id = _genId();
    const entry: DialogEntry = {
      id,
      state: reactive(_defaultState()),
      visible: true,
      resolved: false,
      streaming: state.streaming === true,
      streamingCallback: null,
    };
    delete (state as unknown as Record<string, unknown>).streaming;
    Object.assign(entry.state, state);
    dialogs.value = [...dialogs.value, entry];
    return id;
  }

  /** 关闭弹窗（从列表中移除） */
  function closeDialog(id: string) {
    dialogs.value = dialogs.value.filter(e => e.id !== id);
    if (lastMinimizedId.value === id) lastMinimizedId.value = null;
  }

  /** 更新弹窗内容（流式弹窗逐 chunk 填入） */
  function updateDialog(id: string, partial: Partial<DialogState>) {
    const entry = dialogs.value.find(e => e.id === id);
    if (entry) {
      // 流式更新 pages 时不主动覆盖 currentPageIndex，保留用户手动翻页位置
      if (partial.pages !== undefined && partial.currentPageIndex === undefined) {
        delete partial.currentPageIndex;
      }
      Object.assign(entry.state, partial);
    }
  }

  /** 标记流式弹窗为已完成 */
  function finalizeDialog(id: string) {
    const entry = dialogs.value.find(e => e.id === id);
    if (entry) entry.streaming = false;
  }

  /** 最小化弹窗——隐藏但不取消，ToolCard 可恢复 */
  function minimizeDialog(id: string) {
    const entry = dialogs.value.find(e => e.id === id);
    if (entry) {
      entry.visible = false;
      lastMinimizedId.value = id;
    }
  }

  /** 恢复被最小化的弹窗 */
  function restoreDialog(id?: string) {
    const targetId = id || lastMinimizedId.value;
    if (!targetId) return;
    const entry = dialogs.value.find(e => e.id === targetId);
    if (entry) {
      entry.visible = true;
      // 不重置 resolved——ToolCard 通过它判断是否已被处理
    }
  }

  /** 设置流式弹窗的回调（后端通过 WebSocket 等待响应时使用） */
  function setStreamingCallback(id: string, cb: (result: Record<string, unknown> | null) => void) {
    const entry = dialogs.value.find(e => e.id === id);
    if (entry) entry.streamingCallback = cb;
  }

  // ═══════════════════════════════════════════════════════════════
  // ToolCard 兼容：通过 lastMinimizedId 读写 resolve 状态
  // ═══════════════════════════════════════════════════════════════

  /** 最后一个被最小化的弹窗是否已 resolved（ToolCard 用） */
  const dialogResolved = computed({
    get: () => {
      const id = lastMinimizedId.value;
      if (!id) return false;
      const entry = dialogs.value.find(e => e.id === id);
      return entry ? entry.resolved : false;
    },
    set: (val: boolean) => {
      const id = lastMinimizedId.value;
      if (!id) return;
      const entry = dialogs.value.find(e => e.id === id);
      if (entry) entry.resolved = val;
    },
  });

  // ═══════════════════════════════════════════════════════════════
  // 其他 UI 方法
  // ═══════════════════════════════════════════════════════════════

  function toggleSidebar() {
    sidebarCollapsed.value = !sidebarCollapsed.value;
    localStorage.setItem(STORAGE_KEYS.SIDEBAR_COLLAPSED, String(sidebarCollapsed.value));
  }
  function toggleThinkToolsOpen() {
    thinkToolsOpen.value = !thinkToolsOpen.value;
    localStorage.setItem(STORAGE_KEYS.THINK_TOOLS_OPEN, String(thinkToolsOpen.value));
  }
  function toggleContentExpanded() {
    contentExpanded.value = !contentExpanded.value;
    localStorage.setItem(STORAGE_KEYS.CONTENT_EXPANDED, String(contentExpanded.value));
  }
  function toggleAllowToggle() {
    allowToggle.value = !allowToggle.value;
    localStorage.setItem(STORAGE_KEYS.ALLOW_TOGGLE, String(allowToggle.value));
  }
  /** 设置全局字号并同步到 DOM + localStorage */
  function setFontSize(size: number) {
    fontSize.value = Math.min(20, Math.max(12, size));
    localStorage.setItem(STORAGE_KEYS.FONT_SIZE, String(fontSize.value));
    document.documentElement.style.fontSize = fontSize.value + 'px';
  }
/** 工厂：创建 (fetch + toggle) 方法对，消除 6 个方法的重复代码 */
function _createTogglePair(apiUrl: string, ref: ReturnType<typeof import('vue').ref<boolean>>) {
  async function _fetch() {
    try {
      const { useAuthStore } = await import('@/stores/auth')
      const { apiPath } = await import('@/utils/api')
      const auth = useAuthStore()
      const resp = await fetch(apiPath(apiUrl), {
        headers: { 'X-Access-Password': auth.password },
      })
      if (resp.ok) {
        const data = await resp.json()
        ref.value = data.enabled === true
      }
    } catch { /* 后端未启动时忽略 */ }
  }
  async function _toggle() {
    const next = !ref.value
    try {
      const { useAuthStore } = await import('@/stores/auth')
      const { apiPath } = await import('@/utils/api')
      const auth = useAuthStore()
      const resp = await fetch(apiPath(apiUrl), {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'X-Access-Password': auth.password,
        },
        body: JSON.stringify({ enabled: next }),
      })
      if (resp.ok) ref.value = next
    } catch { /* 网络错误忽略 */ }
  }
  return { fetch: _fetch, toggle: _toggle }
}

// 实例化三组 (fetch + toggle)
const notif = _createTogglePair('/api/notification', notificationEnabled)
const visionRes = _createTogglePair('/api/vision-high-res', visionHighRes)
const browserHead = _createTogglePair('/api/browser-headed', browserHeaded)
const fetchNotificationStatus = notif.fetch
const toggleNotification = notif.toggle
const fetchVisionHighRes = visionRes.fetch
const toggleVisionHighRes = visionRes.toggle
const fetchBrowserHeaded = browserHead.fetch
const toggleBrowserHeaded = browserHead.toggle

  function setAutoScroll(val: boolean) { autoScroll.value = val; }

  function showToast(msg: string, duration = 2000) {
    toastMessage.value = msg;
    toastVisible.value = true;
    if (toastTimer) clearTimeout(toastTimer);
    toastTimer = setTimeout(() => { toastVisible.value = false; toastTimer = null; }, duration);
  }

  function showContextMenu(menu: ContextMenuState) { contextMenu.value = menu; }
  function hideContextMenu() { contextMenu.value = null; }
  function requestFocusInput() { focusInputRequest.value++ }

  return {
    sidebarCollapsed, thinkToolsOpen, contentExpanded, allowToggle, notificationEnabled, visionHighRes, browserHeaded, autoScroll, programScrolling,
    focusInputRequest, requestFocusInput, isComposing,
    toastMessage, toastVisible, contextMenu,
    // 弹窗 API
    dialogs, lastMinimizedId,
    showDialog, closeDialog, updateDialog, finalizeDialog, minimizeDialog, restoreDialog,
    setStreamingCallback,
    // ToolCard 兼容
    dialogResolved,
    // 其他
    toggleSidebar, toggleThinkToolsOpen, toggleContentExpanded, toggleAllowToggle,
    fetchNotificationStatus, toggleNotification,
    fetchVisionHighRes, toggleVisionHighRes,
    fetchBrowserHeaded, toggleBrowserHeaded,
    setAutoScroll,
    fontSize, setFontSize,
    showToast, showContextMenu, hideContextMenu,
  };
});