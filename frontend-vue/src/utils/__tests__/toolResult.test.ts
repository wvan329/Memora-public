import { describe, it, expect } from 'vitest';
import { getDialogType, applyToolResult } from '../toolResult';
import type { ToolItemState } from '@/types/chat';

// ═══════════════════════════════════════════════════════════════
// getDialogType
// ═══════════════════════════════════════════════════════════════
describe('getDialogType', () => {
  it('ask_user → ask_user', () => expect(getDialogType('ask_user')).toBe('ask_user'));
  it('schedule_restart → confirm', () => expect(getDialogType('schedule_restart')).toBe('confirm'));
  it('install_apk → confirm', () => expect(getDialogType('install_apk')).toBe('confirm'));
  it('vision_understand → vision', () => expect(getDialogType('vision_understand')).toBe('vision'));
  it('ai_delegate → delegate', () => expect(getDialogType('ai_delegate')).toBe('delegate'));
  it('browser_task → delegate', () => expect(getDialogType('browser_task')).toBe('delegate'));
  it('compress_context → delegate', () => expect(getDialogType('compress_context')).toBe('delegate'));
  it('未知工具 → undefined', () => expect(getDialogType('unknown_tool')).toBeUndefined());
  it('空字符串 → undefined', () => expect(getDialogType('')).toBeUndefined());
});

// ═══════════════════════════════════════════════════════════════
// applyToolResult
// ═══════════════════════════════════════════════════════════════
function makeItem(overrides: Partial<ToolItemState> = {}): ToolItemState {
  return { name: 'test', args: '', result: '', resultType: 'json', ...overrides };
}

describe('applyToolResult', () => {
  it('download 类型 → resultType 设为 download，标记 _done', () => {
    const item = makeItem();
    applyToolResult(item, '{"type":"download","url":"/a.zip","filename":"a.zip","size":1024}');
    expect(item.resultType).toBe('download');
    expect(item._done).toBe(true);
  });

  it('image 类型 → resultType 设为 image', () => {
    const item = makeItem();
    applyToolResult(item, '{"type":"image","url":"/x.png","filename":"x.png"}');
    expect(item.resultType).toBe('image');
    expect(item._done).toBe(true);
  });

  it('vision_result 类型 → 同时设置 images', () => {
    const item = makeItem();
    applyToolResult(item, '{"type":"vision_result","images":[{"url":"/a.jpg","size":"100KB"}],"text":"结果"}');
    expect(item.resultType).toBe('vision_result');
    expect(item.images).toHaveLength(1);
    expect(item.images![0].url).toBe('/a.jpg');
  });

  it('files 类型 → 同时设置 files', () => {
    const item = makeItem();
    applyToolResult(item, '{"type":"files","items":[{"type":"download","url":"/a.txt","filename":"a.txt"}]}');
    expect(item.resultType).toBe('files');
    expect(item.files).toHaveLength(1);
  });

  it('单任务 delegate → 设置 sessionUuid', () => {
    const item = makeItem();
    applyToolResult(item, '{"session_uuid":"abc-123","result":"done"}');
    expect(item.resultType).toBe('delegate');
    expect(item.sessionUuid).toBe('abc-123');
  });

  it('批量 delegate → 设置 batchSessions', () => {
    const item = makeItem();
    applyToolResult(item, '{"sessions":[{"session_uuid":"x","index":0,"task":"t1","success":true}]}');
    expect(item.resultType).toBe('delegate');
    expect(item.batchSessions).toHaveLength(1);
    expect(item.batchSessions![0].session_uuid).toBe('x');
  });

  it('pending placeholder → _done 保持 false', () => {
    const item = makeItem();
    applyToolResult(item, '{"pending":true}');
    expect(item._done).toBe(false);
  });

  it('非 JSON 内容 → resultType 保持原值或 json', () => {
    const item = makeItem();
    applyToolResult(item, 'plain text result');
    expect(item.result).toBe('plain text result');
    expect(item.resultType).toBe('json');
  });

  it('已有 delegate resultType 不会被纯文本覆盖', () => {
    const item = makeItem({ resultType: 'delegate' });
    applyToolResult(item, 'plain text');
    expect(item.resultType).toBe('delegate');  // 保留
  });

  it('解析失败 → 保留 content 原文', () => {
    const item = makeItem();
    applyToolResult(item, 'not json at all');
    expect(item.result).toBe('not json at all');
  });
});
