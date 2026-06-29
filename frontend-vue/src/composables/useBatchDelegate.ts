// ========================= useBatchDelegate =========================
// 管理 ai_delegate / browser_task 批量委托的流式弹窗。
//
// v4.3 重构：
//   - 消除模块级变量 _batchArgsBuffer / _batchToolCallId，通过 batchStates Map 自管理
//   - 修复 routeChunk 丢弃 bug：无法匹配时暂存 pending 而非丢弃
//   - 修复完成标记：tool_result 解析失败时也追加完成标记并 finalize
//   - 直接用 ui.showDialog / ui.updateDialog / ui.finalizeDialog
//
// v4.5：
//   - routeChunk 兜底增强：batchStates 为空时回退查找 delegate tool block，避免 chunk 泄露到主消息
//   - 子任务逐一出队完成：backend 新增 sub_task_end 双推，每个子 AI 各自完成时立即追加 ✅ 标记
//   - tool_result 处理防重复：已由 sub_task_end 标记完成的页面不再追加标记

import { useUIStore } from '@/stores/ui';
import { useChatStore } from '@/stores/chat';
import { extractTaskFieldsFromPartial } from '@/utils/streamJsonParser';
import { getDialogType } from '@/utils/toolResult';
import type { StreamChunk } from '@/types/ws';
import type { DialogPage } from '@/types/ui';

type RawMsg = Record<string, unknown>;

// ── 批量委托弹窗页面类型 ──
interface BatchPage extends DialogPage {
  message: string;
  options: string[];
  confirm_text: string;
  cancel_text: string;
}

// ── 批量委托状态 ──
interface BatchDelegateState {
  dialogId: string;
  toolCallId: string;
  pages: BatchPage[];
  sessionToPage: Map<string, number>;
  pageContents: string[];
  /** 标记哪些页面已被 appendPageText 写入过真实内容（不再允许 buildPages 用模板覆盖） */
  pageSeeded: boolean[];
  pendingChunks: Map<string, string[]>;
  argsBuffer: string;
}

export function useBatchDelegate() {
  const ui = useUIStore();
  const chat = useChatStore();

  // tool_call_id → 批次状态
  const batchStates = new Map<string, BatchDelegateState>();
  // session_uuid → tool_call_id（子 AI chunk 路由用）
  const sessionToBatch = new Map<string, string>();

  // pendingChunks 中表示子任务完成的哨兵值
  const SUB_TASK_END = '__SUB_TASK_END__';

  // ═══════════════════════════════════════════════════════════════
  // 内部工具方法
  // ═══════════════════════════════════════════════════════════════

  /** 将 pageContents 同步到 pages 并刷新弹窗 UI */
  function syncPages(state: BatchDelegateState) {
    state.pages = state.pages.map((p, i) => ({
      ...p,
      message: state.pageContents[i] || p.message,
    }));
    ui.updateDialog(state.dialogId, {
      pages: state.pages,
      message: state.pages[0]?.message || '',
    });
  }

  function ensureBatchState(toolCallId: string): BatchDelegateState {
    let state = batchStates.get(toolCallId);
    if (!state) {
      const dialogId = ui.showDialog({
        message: '⏳ 正在启动…',
        options: [],
        confirmText: '确认',
        cancelText: '取消',
        showInput: false,
        hideActions: true,
        streaming: true,
      });
      state = {
        dialogId,
        toolCallId,
        pages: [],
        sessionToPage: new Map(),
        pageContents: [],
        pageSeeded: [],
        pendingChunks: new Map(),
        argsBuffer: '',
      };
      batchStates.set(toolCallId, state);
    }
    return state;
  }

  /** 将文本追加到指定 session 的弹窗页面 */
  function appendPageText(state: BatchDelegateState, sessionUuid: string, text: string) {
    const pageIdx = state.sessionToPage.get(sessionUuid);
    if (pageIdx === undefined) {
      // 映射尚未建立 → 暂存
      if (!state.pendingChunks.has(sessionUuid)) {
        state.pendingChunks.set(sessionUuid, []);
      }
      state.pendingChunks.get(sessionUuid)!.push(text);
      return;
    }
    if (pageIdx >= state.pageContents.length) return;
    state.pageContents[pageIdx] += text;
    state.pageSeeded[pageIdx] = true;
    syncPages(state);
  }

  /** 为指定子任务追加完成标记并更新弹窗 */
  function appendCompletionMarker(state: BatchDelegateState, sessUuid: string) {
    const pageIdx = state.sessionToPage.get(sessUuid);
    if (pageIdx === undefined || pageIdx >= state.pageContents.length) return;
    // 避免重复标记
    if (state.pageContents[pageIdx].includes('✅ **完成**')) return;
    state.pageContents[pageIdx] += '\n\n---\n✅ **完成**';
    state.pageSeeded[pageIdx] = true;
    syncPages(state);
  }

  /** 回放映射建立前到达的 chunk */
  function replayPending(state: BatchDelegateState, sessionUuid: string) {
    const pending = state.pendingChunks.get(sessionUuid);
    if (!pending || pending.length === 0) return;
    const pi = state.sessionToPage.get(sessionUuid);
    if (pi === undefined || pi >= state.pageContents.length) { state.pendingChunks.delete(sessionUuid); return; }
    for (const t of pending) {
      // sub_task_end 的暂存标记 → 追加完成标记而非文本
      if (t === SUB_TASK_END) {
        if (!state.pageContents[pi].includes('✅ **完成**')) {
          state.pageContents[pi] += '\n\n---\n✅ **完成**';
        }
        continue;
      }
      state.pageContents[pi] += t;
    }
    state.pendingChunks.delete(sessionUuid);
    state.pageSeeded[pi] = true;
    syncPages(state);
  }

  /** 从工具参数构建弹窗页面 */
  function buildPages(state: BatchDelegateState, tasks: string[]) {
    if (tasks.length < state.pages.length) return;

    const newPages = tasks.map((task, i) => {
      const msg = `<div class="text-sm text-gray-500 dark:text-gray-400 mb-2">📋 任务 ${i + 1}: ${typeof task === 'object' && task !== null ? String((task as Record<string, unknown>).task || '') : String(task)}</div>\n\n\n`;
      if (i < state.pages.length) {
        // 页面已被 appendPageText 写入过真实内容 → 保留，不覆盖
        if (state.pageSeeded[i]) return state.pages[i];
        // 未写入过 → 可用更完整的任务描述更新模板
        state.pageContents[i] = msg;
        return { ...state.pages[i], message: msg };
      }
      return { message: msg, options: [] as string[], confirm_text: '确认', cancel_text: '取消' };
    });

    state.pages = newPages;
    while (state.pageContents.length < tasks.length) {
      state.pageContents.push(newPages[state.pageContents.length].message);
    }
    // 同步 pageSeeded 长度
    while (state.pageSeeded.length < tasks.length) {
      state.pageSeeded.push(false);
    }

    syncPages(state);
  }

  /** 增量解析工具参数中的 tasks */
  function tryParseArgs(state: BatchDelegateState, chunk: string) {
    state.argsBuffer += chunk;
    try {
      const parsed = JSON.parse(state.argsBuffer);
      const rawTasks = (parsed as Record<string, unknown>).tasks as Array<Record<string, unknown>> | undefined;
      if (rawTasks && Array.isArray(rawTasks) && rawTasks.length > 0) {
        const taskDescs = rawTasks.map(t => (t.task as string) || '');
        buildPages(state, taskDescs);
      }
    } catch {
      const taskDescs = extractTaskFieldsFromPartial(state.argsBuffer);
      if (taskDescs.length > 0) {
        buildPages(state, taskDescs);
      }
    }
  }

  // ═══════════════════════════════════════════════════════════════
  // 对外接口
  // ═══════════════════════════════════════════════════════════════

  /**
   * 路由子 AI 的 text/reason/sub_task_end chunk 到批量弹窗页面。
   * 返回 true 表示 chunk 已消费，调用方应跳过 processChunk。
   */
  function routeChunk(c: StreamChunk): boolean {
    // ── sub_task_end：子任务流式完成通知（v4.5 新增）──
    if (c.type === 'sub_task_end') {
      const sessUuid = (c as Record<string, unknown>).session_uuid as string | undefined;
      if (!sessUuid) return true; // 无法路由但也不泄露到 processChunk
      const batchTcId = sessionToBatch.get(sessUuid);
      if (batchTcId) {
        const state = batchStates.get(batchTcId);
        if (state) { appendCompletionMarker(state, sessUuid); return true; }
      }
      // 映射未就绪 → 暂存完成标记（和 text chunk 同样逻辑）
      if (batchStates.size > 0) {
        for (const [, state] of batchStates) {
          if (!state.pendingChunks.has(sessUuid)) {
            state.pendingChunks.set(sessUuid, []);
          }
          state.pendingChunks.get(sessUuid)!.push(SUB_TASK_END);
        }
        return true;
      }
      return true; // batchStates 为空也无法路由，但仍消费掉
    }

    if (c.type !== 'text' && c.type !== 'reason') return false;
    const sessUuid = (c as Record<string, unknown>).session_uuid as string | undefined;
    if (!sessUuid) return false;

    const batchTcId = sessionToBatch.get(sessUuid);
    if (batchTcId) {
      const state = batchStates.get(batchTcId);
      if (state) {
        appendPageText(state, sessUuid, c.content || '');
        return true;
      }
    }

    // ★ v4.3 修复：无法匹配时暂存到所有活跃批次的 pendingChunks，
    // 等 delegate_batch_start 建立映射后回放。不丢弃。
    if (batchStates.size > 0) {
      for (const [, state] of batchStates) {
        if (!state.pendingChunks.has(sessUuid)) {
          state.pendingChunks.set(sessUuid, []);
        }
        state.pendingChunks.get(sessUuid)!.push(c.content || '');
      }
      return true;  // 已暂存，不泄露到主 AI 结果区
    }

    // ★ v4.5 修复：batchStates 为空但 chunk 带 session_uuid 和 tool_call_id，
    // 说明弹窗尚未创建（可能 delegate_batch_start 在历史重放中丢失或乱序）。
    // 回退查找主消息中的 delegate tool block，若存在则主动恢复 batch state 并暂存 chunk，
    // 避免泄露到 processChunk → 追加到主 AI ToolCard result。
    const tcId = (c as Record<string, unknown>).tool_call_id as string | undefined;
    if (tcId && sessUuid) {
      const msg = chat.getStreamingMessage();
      if (msg) {
        const block = msg.thinkBlocks.find(
          b => b.type === 'tool' && b.key === tcId && b.item.dialogType === 'delegate'
        );
        if (block) {
          const state = ensureBatchState(tcId);
          chat.setToolDialogId(tcId, state.dialogId);
          if (!state.pendingChunks.has(sessUuid)) {
            state.pendingChunks.set(sessUuid, []);
          }
          state.pendingChunks.get(sessUuid)!.push(c.content || '');
          return true;
        }
      }
    }

    // 真正无法匹配 → 回退到 ToolCard 渲染（可能泄露，但已尽力兜底）
    return false;
  }

  /**
   * 处理批量工具的参数/结果 chunk，管理弹窗创建和关闭。
   */
  function handleToolChunk(c: StreamChunk): void {
    // 批量委托工具：创建弹窗 + 增量解析参数
    if (c.type === 'tool_call_name' && getDialogType(c.content) === 'delegate') {
      const tcId = c.tool_call_id || '';
      const state = ensureBatchState(tcId);
      state.argsBuffer = '';
      chat.setToolDialogId(tcId, state.dialogId);
    } else if (c.type === 'tool_call_args' && c.tool_call_id) {
      const state = batchStates.get(c.tool_call_id);
      if (state) tryParseArgs(state, c.content || '');
    } else if (c.type === 'tool_call_info' && c.tool_call_id) {
      const state = batchStates.get(c.tool_call_id);
      if (state) tryParseArgs(state, JSON.stringify(c.arguments || {}));
    }

    // 批量委托完成：tool_result 到达时标记完成
    if (c.type === 'tool_result' && c.tool_call_id) {
      const state = batchStates.get(c.tool_call_id);
      if (!state) return;

      // 尝试解析 sessions 获取每个子任务的结果
      let hasSessions = false;
      let isAborted = false;
      try {
        const parsed = JSON.parse(c.content || '{}');
        isAborted = parsed?.aborted === true;
        const sessions = parsed?.sessions as Array<Record<string, unknown>> | undefined;
        if (sessions) {
          hasSessions = true;
          for (const sess of sessions) {
            const sessUuid = sess.session_uuid as string;
            const pageIdx = state.sessionToPage.get(sessUuid);
            if (pageIdx !== undefined && pageIdx < state.pageContents.length) {
              // ★ v4.5：sub_task_end 可能已追加完成标记，避免重复
              if (state.pageContents[pageIdx].includes('✅ **完成**') ||
                  state.pageContents[pageIdx].includes('❌ **错误**')) {
                continue;
              }
              const marker = sess.success
                ? '\n\n---\n✅ **完成**'
                : `\n\n---\n❌ **错误**: ${sess.error || '未知错误'}`;
              state.pageContents[pageIdx] += marker;
            }
          }
        }
      } catch { /* ignore */ }

      // ★ v4.3 修复：即使解析失败或缺少 sessions，也追加完成标记并 finalize
      if (!hasSessions) {
        const marker = isAborted
          ? '\n\n---\n🛑 **用户主动终止**'
          : '\n\n---\n✅ **完成**';
        for (let i = 0; i < state.pageContents.length; i++) {
          // v4.5：同样避免重复
          if (state.pageContents[i].includes('✅ **完成**') ||
              state.pageContents[i].includes('🛑 **用户主动终止**')) {
            continue;
          }
          state.pageContents[i] += marker;
        }
      }

      syncPages(state);
      ui.finalizeDialog(state.dialogId);
      // 完成后自动关闭弹窗
      const dialogId = state.dialogId;
      ui.closeDialog(dialogId);
      batchStates.delete(c.tool_call_id);
    }
  }

  /**
   * 处理 delegate_batch_start 消息：建立 session_uuid → page 映射。
   */
  function handleDelegateBatchStart(msg: RawMsg): void {
    const toolCallId = String(msg.tool_call_id || '');
    const sessions = msg.sessions as Array<Record<string, unknown>> | undefined;
    if (!sessions || !Array.isArray(sessions)) return;

    chat.markToolDelegate(toolCallId);

    const state = ensureBatchState(toolCallId);
    chat.setToolDialogId(toolCallId, state.dialogId);

    for (const sess of sessions) {
      const sessUuid = String(sess.session_uuid || '');
      const index = Number(sess.index ?? -1);
      if (sessUuid && index >= 0) {
        state.sessionToPage.set(sessUuid, index);
        sessionToBatch.set(sessUuid, toolCallId);
        // replayPending 延迟到 pages 初始化之后
      }
    }

    // ★ 修复 compress_context 弹窗空白：
    // compress_context 不走 arg 解析路径（handleToolChunk 只匹配 ai_delegate/browser_task），
    // 导致 buildPages 从未被调用 → pageContents 为空 → appendPageText 静默丢弃所有文本。
    // 此处直接用 sessions 数据初始化 pages，确保所有触发 delegate_batch_start 的工具都能正常展示子 AI 输出。
    if (state.pages.length === 0 && sessions.length > 0) {
      const taskDescs = sessions
        .sort((a, b) => Number(a.index ?? 0) - Number(b.index ?? 0))
        .map(s => String(s.task || ''));
      buildPages(state, taskDescs);
    }

    // 建立映射 + pages 初始化后再回放 pending
    for (const sess of sessions) {
      const sessUuid = String(sess.session_uuid || '');
      const index = Number(sess.index ?? -1);
      if (sessUuid && index >= 0) {
        replayPending(state, sessUuid);
      }
    }
  }

  function reset() {
    batchStates.clear();
    sessionToBatch.clear();
  }

  return { routeChunk, handleToolChunk, handleDelegateBatchStart, reset };
}