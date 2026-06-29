// ========================= 历史消息构建 =========================
// 将原始 ChatMessage[] 转为 DisplayMessage[]。
// 从 chat store 中提取以减少 store 体积，同时便于单元测试。

import type { ChatMessage } from '@/types/ws';
import type { DisplayMessage, ThinkBlock, ToolBlock } from '@/stores/chat';
import { applyToolResult, getDialogType } from './toolResult';

function msgId(): string {
  return 'm_' + Date.now() + '_' + Math.random().toString(36).slice(2, 8);
}

/** 将原始 ChatMessage[] 转为 DisplayMessage[]。连续的 assistant+tool 归为一个 AI 消息。 */
export function buildDisplayMessages(msgs: ChatMessage[]): DisplayMessage[] {
  const result: DisplayMessage[] = [];
  let i = 0;
  while (i < msgs.length) {
    const m = msgs[i];
    if (m.role === 'system') { result.push({ id: msgId(), role: 'system', content: m.content || '', thinkBlocks: [] }); i++; }
    else if (m.role === 'user') { result.push({ id: msgId(), role: 'user', content: m.content || '', thinkBlocks: [], turnId: m.turn_id }); i++; }
    else {
      const group: ChatMessage[] = [];
      while (i < msgs.length && msgs[i].role !== 'user' && msgs[i].role !== 'system') { group.push(msgs[i]); i++; }
      result.push(buildOneAI(group));
    }
  }
  return result;
}

/** 将一组 assistant+tool 消息转为单个 DisplayMessage。
 *  遍历 group，按原始顺序插入 thinkBlocks（reasoning 块和 tool 块交错）。 */
export function buildOneAI(group: ChatMessage[]): DisplayMessage {
  const blocks: ThinkBlock[] = [];
  const toolMap = new Map<string, ToolBlock>();
  let text = '';
  for (const m of group) {
    if (m.reasoning_content?.trim()) blocks.push({ type: 'reasoning', text: m.reasoning_content.trim() });
    if (m.tool_calls) {
      let tcs: { id?: string; function?: { name: string; arguments: string } }[] = [];
      if (typeof m.tool_calls === 'string') { try { tcs = JSON.parse(m.tool_calls); } catch { /* */ } } else if (Array.isArray(m.tool_calls)) tcs = m.tool_calls;
      tcs.forEach((tc, idx) => {
        const key = tc.id || `idx_${idx}`; let args = tc.function?.arguments || '';
        if (typeof args === 'string') { try { args = JSON.stringify(JSON.parse(args), null, 2); } catch { /* */ } } else args = JSON.stringify(args, null, 2);
        const name = tc.function?.name || '未知工具';
        const dt = getDialogType(name);
        const block: ToolBlock = { type: 'tool', key, item: { name, args, result: '', resultType: dt === 'delegate' ? 'delegate' : 'json', dialogType: dt } };
        blocks.push(block); toolMap.set(key, block);
      });
    }
    if (m.role === 'tool' && m.tool_call_id) {
      const block = toolMap.get(m.tool_call_id);
      if (block) { applyToolResult(block.item, m.content || ''); }
    }
    if (m.role === 'assistant' && m.content) text += (text ? '\n\n' : '') + m.content.trim();
  }
  return { id: msgId(), role: 'ai', content: text, thinkBlocks: blocks };
}
