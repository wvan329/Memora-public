import { describe, it, expect } from 'vitest';
import { buildDisplayMessages, buildOneAI } from '../messageBuilder';
import type { ChatMessage } from '@/types/ws';

// ═══════════════════════════════════════════════════════════════
// buildOneAI
// ═══════════════════════════════════════════════════════════════
describe('buildOneAI', () => {
  it('纯文本 assistant → 只有 content，无 thinkBlocks', () => {
    const group: ChatMessage[] = [{ role: 'assistant', content: 'Hello world' }];
    const result = buildOneAI(group);
    expect(result.role).toBe('ai');
    expect(result.content).toBe('Hello world');
    expect(result.thinkBlocks).toHaveLength(0);
  });

  it('含 reasoning 的 assistant → thinkBlocks 有 reasoning 块', () => {
    const group: ChatMessage[] = [
      { role: 'assistant', content: '', reasoning_content: 'thinking...' },
      { role: 'assistant', content: 'Answer' },
    ];
    const result = buildOneAI(group);
    expect(result.thinkBlocks).toHaveLength(1);
    expect(result.thinkBlocks[0].type).toBe('reasoning');
    expect((result.thinkBlocks[0] as { text: string }).text).toBe('thinking...');
    expect(result.content).toBe('Answer');
  });

  it('含 tool_call 的 assistant → thinkBlocks 有 tool 块', () => {
    const group: ChatMessage[] = [
      { role: 'assistant', tool_calls: '[{"id":"1","function":{"name":"ask_user","arguments":"{\\"pages\\":[]}"}}]' },
    ];
    const result = buildOneAI(group);
    expect(result.thinkBlocks).toHaveLength(1);
    const toolBlock = result.thinkBlocks[0];
    expect(toolBlock.type).toBe('tool');
    expect(toolBlock).toHaveProperty('item');
  });

  it('多个 tool_calls → 多个 tool block', () => {
    const group: ChatMessage[] = [
      { role: 'assistant', tool_calls: '[{"id":"1","function":{"name":"read","arguments":"{}"}},{"id":"2","function":{"name":"write","arguments":"{}"}}]' },
    ];
    const result = buildOneAI(group);
    expect(result.thinkBlocks.filter(b => b.type === 'tool')).toHaveLength(2);
  });

  it('tool_call + tool 响应 → tool block 会被填入结果', () => {
    const group: ChatMessage[] = [
      { role: 'assistant', tool_calls: '[{"id":"abc","function":{"name":"ask_user","arguments":"{\\"pages\\":[]}"}}]' },
      { role: 'tool', content: '{"selected":"A"}', tool_call_id: 'abc' },
    ];
    const result = buildOneAI(group);
    const toolBlocks = result.thinkBlocks.filter(b => b.type === 'tool');
    expect(toolBlocks).toHaveLength(1);
    expect(toolBlocks[0].item._done).toBe(true);
    expect(toolBlocks[0].item.result).toBe('{\n  "selected": "A"\n}');
  });

  it('reasoning 和 tool 交错时保持顺序', () => {
    const group: ChatMessage[] = [
      { role: 'assistant', reasoning_content: 'think 1', tool_calls: '[{"id":"1","function":{"name":"read","arguments":"{}"}}]' },
      { role: 'tool', content: 'ok', tool_call_id: '1' },
      { role: 'assistant', reasoning_content: 'think 2' },
      { role: 'assistant', content: 'final answer' },
    ];
    const result = buildOneAI(group);
    // 顺序：reasoning(think1) → tool(read) → reasoning(think2)
    expect(result.thinkBlocks[0].type).toBe('reasoning');
    expect(result.thinkBlocks[1].type).toBe('tool');
    expect(result.thinkBlocks[2].type).toBe('reasoning');
  });

  it('tool_calls 为字符串格式也能解析', () => {
    const group: ChatMessage[] = [
      { role: 'assistant', tool_calls: JSON.stringify([{ id: 'x', function: { name: 'ask_user', arguments: '{}' } }]) },
    ];
    const result = buildOneAI(group);
    expect(result.thinkBlocks).toHaveLength(1);
  });

  it('未知工具名 → 标记为"未知工具"', () => {
    const group: ChatMessage[] = [
      { role: 'assistant', tool_calls: '[{"id":"1","function":{"name":"","arguments":"{}"}}]' },
    ];
    const result = buildOneAI(group);
    expect((result.thinkBlocks[0] as { item: { name: string } }).item.name).toBe('未知工具');
  });
});

// ═══════════════════════════════════════════════════════════════
// buildDisplayMessages
// ═══════════════════════════════════════════════════════════════
describe('buildDisplayMessages', () => {
  it('user → assistant 交替', () => {
    const msgs: ChatMessage[] = [
      { role: 'user', content: 'Hi' },
      { role: 'assistant', content: 'Hello' },
    ];
    const result = buildDisplayMessages(msgs);
    expect(result).toHaveLength(2);
    expect(result[0].role).toBe('user');
    expect(result[1].role).toBe('ai');
  });

  it('连续的 assistant+tool 合并为一条 AI 消息', () => {
    const msgs: ChatMessage[] = [
      { role: 'user', content: 'Q' },
      { role: 'assistant', content: '', tool_calls: '[{"id":"x","function":{"name":"read","arguments":"{}"}}]' },
      { role: 'tool', content: 'ok', tool_call_id: 'x' },
      { role: 'assistant', content: 'A' },
    ];
    const result = buildDisplayMessages(msgs);
    expect(result).toHaveLength(2); // user + 合并后的 AI
    expect(result[1].role).toBe('ai');
  });

  it('system 消息单独展示', () => {
    const msgs: ChatMessage[] = [{ role: 'system', content: 'System info' }];
    const result = buildDisplayMessages(msgs);
    expect(result[0].role).toBe('system');
  });

  it('空数组 → 空数组', () => {
    expect(buildDisplayMessages([])).toEqual([]);
  });

  it('多条 user-assistant 交替', () => {
    const msgs: ChatMessage[] = [
      { role: 'user', content: 'Q1' },
      { role: 'assistant', content: 'A1' },
      { role: 'user', content: 'Q2' },
      { role: 'assistant', content: 'A2' },
    ];
    const result = buildDisplayMessages(msgs);
    expect(result).toHaveLength(4);
    expect(result.map(m => m.role)).toEqual(['user', 'ai', 'user', 'ai']);
  });
});
