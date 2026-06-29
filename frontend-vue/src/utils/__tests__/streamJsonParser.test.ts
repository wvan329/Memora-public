import { describe, it, expect } from 'vitest';
import {
  extractBracedObject,
  extractStringField,
  extractTaskFieldsFromPartial,
  extractPagesFromPartial,
  extractPartialMessageFromLastPage,
} from '../streamJsonParser';

// ═══════════════════════════════════════════════════════════════
// extractBracedObject
// ═══════════════════════════════════════════════════════════════
describe('extractBracedObject', () => {
  it('应提取完整闭合的顶层对象', () => {
    expect(extractBracedObject('{"a":1}', 0)).toBe('{"a":1}');
  });

  it('应处理嵌套对象', () => {
    expect(extractBracedObject('{"a":{"b":2}}', 0)).toBe('{"a":{"b":2}}');
  });

  it('应处理字符串内的大括号（不误判为嵌套）', () => {
    expect(extractBracedObject('{"a":"{not brace}"}', 0)).toBe('{"a":"{not brace}"}');
  });

  it('不完整对象返回 null', () => {
    expect(extractBracedObject('{"a":1', 0)).toBeNull();
  });

  it('转义引号不干扰括号计数', () => {
    expect(extractBracedObject('{"a":"\\"escaped\\""}', 0)).toBe('{"a":"\\"escaped\\""}');
  });

  it('start 位置非 { 返回 null', () => {
    expect(extractBracedObject('abc{"x":1}', 0)).toBeNull();
  });

  it('多对象只提取从 start 开始的第一个', () => {
    expect(extractBracedObject('{"a":1}{"b":2}', 0)).toBe('{"a":1}');
  });

  it('数组内的对象', () => {
    expect(extractBracedObject('[{"x":1}]', 1)).toBe('{"x":1}');
  });
});

// ═══════════════════════════════════════════════════════════════
// extractStringField
// ═══════════════════════════════════════════════════════════════
describe('extractStringField', () => {
  it('应提取简单字符串字段', () => {
    expect(extractStringField('{"task":"hello"}', 0, 'task')).toBe('hello');
  });

  it('字段不存在返回 null', () => {
    expect(extractStringField('{"foo":"bar"}', 0, 'task')).toBeNull();
  });

  it('应处理转义序列', () => {
    expect(extractStringField('{"msg":"hello\\nworld"}', 0, 'msg')).toBe('hello\nworld');
  });

  it('应处理转义引号', () => {
    expect(extractStringField('{"msg":"say \\"hi\\""}', 0, 'msg')).toBe('say "hi"');
  });

  it('不完整的字符串返回已读取部分', () => {
    // 未闭合引号 → 读到末尾
    expect(extractStringField('{"msg":"unfinished', 0, 'msg')).toBe('unfinished');
  });
});

// ═══════════════════════════════════════════════════════════════
// extractTaskFieldsFromPartial
// ═══════════════════════════════════════════════════════════════
describe('extractTaskFieldsFromPartial', () => {
  it('应提取完整闭合的 task 字段', () => {
    const json = '{"tasks":[{"task":"hello"},{"task":"world"}]}';
    expect(extractTaskFieldsFromPartial(json)).toEqual(['hello', 'world']);
  });

  it('应提取半成品中已完成的对象（含不完全对象的 task 字段）', () => {
    const json = '{"tasks":[{"task":"hello"},{"task":"wor';
    // 第一个对象完整闭合 → 'hello'；第二个对象虽然不完整但 extractStringField 可提取 "task" 到末尾 → 'wor'
    expect(extractTaskFieldsFromPartial(json)).toEqual(['hello', 'wor']);
  });

  it('无 tasks 字段返回空数组', () => {
    expect(extractTaskFieldsFromPartial('{"foo":1}')).toEqual([]);
  });

  it('空 tasks 数组返回空数组', () => {
    expect(extractTaskFieldsFromPartial('{"tasks":[]}')).toEqual([]);
  });

  it('tasks 数组中对象缺少 task 字段', () => {
    const json = '{"tasks":[{"x":1},{"task":"ok"}]}';
    expect(extractTaskFieldsFromPartial(json)).toEqual(['ok']);
  });
});

// ═══════════════════════════════════════════════════════════════
// extractPagesFromPartial
// ═══════════════════════════════════════════════════════════════
describe('extractPagesFromPartial', () => {
  it('应提取所有完整闭合的 page', () => {
    const json = '{"pages":[{"message":"Hi","options":["A","B"]},{"message":"Bye"}]}';
    const pages = extractPagesFromPartial(json);
    expect(pages).toHaveLength(2);
    expect(pages[0].message).toBe('Hi');
    expect(pages[1].message).toBe('Bye');
  });

  it('半成品只返回已完成的对象', () => {
    const json = '{"pages":[{"message":"Hi"},{"message":"unf';
    const pages = extractPagesFromPartial(json);
    expect(pages).toHaveLength(1);
    expect(pages[0].message).toBe('Hi');
  });

  it('无 pages 字段返回空数组', () => {
    expect(extractPagesFromPartial('{"other":[]}')).toEqual([]);
  });
});

// ═══════════════════════════════════════════════════════════════
// extractPartialMessageFromLastPage
// ═══════════════════════════════════════════════════════════════
describe('extractPartialMessageFromLastPage', () => {
  it('应提取最后一个未闭合 page 的 message 字段', () => {
    const json = '{"pages":[{"message":"done"},{"message":"in progr';
    expect(extractPartialMessageFromLastPage(json)).toBe('in progr');
  });

  it('所有 page 均已完整闭合时返回 null', () => {
    const json = '{"pages":[{"message":"a"},{"message":"b"}]}';
    expect(extractPartialMessageFromLastPage(json)).toBeNull();
  });

  it('无 pages 字段返回 null', () => {
    expect(extractPartialMessageFromLastPage('{}')).toBeNull();
  });
});
