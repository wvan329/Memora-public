// ========================= 流式 JSON 增量解析 =========================
// LLM 流式输出的 tool_call_args 是逐 token 到达的 JSON 片段。
// 这些纯函数用于从半成品 JSON 字符串中提前提取关键字段（如 pages、tasks），
// 以便在参数完整到达前就开始渲染弹窗 UI。
//
// 为什么手写而不引入第三方库：
// - 流式 JSON 解析库（如 stream-json）是针对标准 JSON 流设计的，
//   需要完整的 token 边界，而 LLM 输出是逐字符到达，没有 token 边界
// - 我们只需要提取少数几个顶层字段，全量解析的开销和复杂度反而更高
// - 手工状态机 250 行，可完全掌控边缘用例

/**
 * 从指定位置提取一个完整的大括号对 `{...}`。
 * 正确处理字符串内的转义和嵌套大括号。
 * 返回完整对象字符串（含花括号），失败返回 null。
 */
export function extractBracedObject(s: string, start: number): string | null {
  if (s[start] !== '{') return null;
  let depth = 0;
  let inStr = false;
  let escape = false;
  for (let i = start; i < s.length; i++) {
    const c = s[i];
    if (escape) { escape = false; continue; }
    if (c === '\\') { escape = true; continue; }
    if (c === '"') { inStr = !inStr; continue; }
    if (inStr) continue;
    if (c === '{') depth++;
    else if (c === '}') {
      depth--;
      if (depth === 0) return s.slice(start, i + 1);
    }
  }
  return null;
}

/**
 * 从对象起始位置提取指定字段的字符串值。
 * 处理转义序列（\" \\ / \n \t \r）。
 * 失败返回 null。
 */
export function extractStringField(s: string, objStart: number, fieldName: string): string | null {
  const keyPattern = `"${fieldName}"`;
  const keyIdx = s.indexOf(keyPattern, objStart);
  if (keyIdx < 0) return null;

  let colonIdx = keyIdx + keyPattern.length;
  while (colonIdx < s.length && s[colonIdx] !== ':') {
    if (!/\s/.test(s[colonIdx])) return null;
    colonIdx++;
  }
  if (colonIdx >= s.length) return null;

  colonIdx++;
  while (colonIdx < s.length && /\s/.test(s[colonIdx])) colonIdx++;
  if (colonIdx >= s.length || s[colonIdx] !== '"') return null;

  let valStart = colonIdx + 1;
  let escape = false;
  let result = '';
  for (let i = valStart; i < s.length; i++) {
    const c = s[i];
    if (escape) {
      switch (c) {
        case '"': result += '"'; break;
        case '\\': result += '\\'; break;
        case '/': result += '/'; break;
        case 'n': result += '\n'; break;
        case 't': result += '\t'; break;
        case 'r': result += '\r'; break;
        default: result += c; break;
      }
      escape = false;
      continue;
    }
    if (c === '\\') { escape = true; continue; }
    if (c === '"') return result;
    result += c;
  }
  return result;
}

/**
 * 从半成品 JSON 的 tasks 数组中提取每个对象的 task 字段值。
 * 用于 ai_delegate / browser_task 的批量委托模式——
 * 在 tasks 数组未完全到达时，提前提取各子任务的描述以便展示弹窗页面。
 */
export function extractTaskFieldsFromPartial(buf: string): string[] {
  const result: string[] = [];
  const keyPattern = '"tasks"';
  const keyIdx = buf.indexOf(keyPattern);
  if (keyIdx < 0) return result;
  const bracketIdx = buf.indexOf('[', keyIdx);
  if (bracketIdx < 0) return result;

  let pos = bracketIdx + 1;
  while (pos < buf.length) {
    while (pos < buf.length && /\s/.test(buf[pos])) pos++;
    if (pos >= buf.length) break;
    if (buf[pos] === ']') break;
    if (buf[pos] === ',') { pos++; continue; }
    if (buf[pos] !== '{') break;

    const obj = extractBracedObject(buf, pos);
    if (!obj) {
      const taskVal = extractStringField(buf, pos, 'task');
      if (taskVal !== null && taskVal.length > 0) result.push(taskVal);
      break;
    }
    try {
      const parsed = JSON.parse(obj);
      if (parsed.task) result.push(parsed.task as string);
      pos += obj.length;
      while (pos < buf.length && /[\s,]/.test(buf[pos])) pos++;
    } catch {
      const taskVal = extractStringField(buf, pos, 'task');
      if (taskVal !== null && taskVal.length > 0) result.push(taskVal);
      break;
    }
  }
  return result;
}

/**
 * 从半成品 JSON 中提取已完整到达的 pages 数组元素。
 * 每个 page 必须是完整闭合的 JSON 对象。
 */
export function extractPagesFromPartial(buf: string): Array<Record<string, unknown>> {
  const pages: Array<Record<string, unknown>> = [];
  const arrStart = buf.indexOf('"pages"');
  if (arrStart < 0) return pages;
  const bracketIdx = buf.indexOf('[', arrStart);
  if (bracketIdx < 0) return pages;

  let pos = bracketIdx + 1;
  while (pos < buf.length) {
    while (pos < buf.length && buf[pos].match(/\s/)) pos++;
    if (pos >= buf.length || buf[pos] !== '{') break;
    const obj = extractBracedObject(buf, pos);
    if (!obj) break;
    try {
      const parsed = JSON.parse(obj);
      pages.push(parsed as Record<string, unknown>);
      pos += obj.length;
      while (pos < buf.length && buf[pos].match(/[\s,]/)) pos++;
    } catch {
      break;
    }
  }
  return pages;
}

/**
 * 提取 pages 数组最后一个未闭合 page 对象中 message 字段的当前值。
 * 用于在流式传输中提前展示最后一条消息的文本。
 */
export function extractPartialMessageFromLastPage(buf: string): string | null {
  const arrStart = buf.indexOf('"pages"');
  if (arrStart < 0) return null;
  const bracketIdx = buf.indexOf('[', arrStart);
  if (bracketIdx < 0) return null;

  let lastBraceIdx = -1;
  let pos = bracketIdx + 1;
  while (pos < buf.length) {
    while (pos < buf.length && /\s/.test(buf[pos])) pos++;
    if (pos >= buf.length) break;
    if (buf[pos] === '{') {
      lastBraceIdx = pos;
      const obj = extractBracedObject(buf, pos);
      if (obj) {
        pos += obj.length;
        while (pos < buf.length && /[\s,]/.test(buf[pos])) pos++;
      } else {
        break;
      }
    } else if (buf[pos] === ']') {
      break;
    } else {
      pos++;
    }
  }

  if (lastBraceIdx < 0) return null;
  // 如果最后一个对象是完整闭合的（循环因遇到 ] 而退出），则没有未闭合的页面
  const lastObj = extractBracedObject(buf, lastBraceIdx);
  if (lastObj) return null;  // 所有页面均已完整，无部分到达的页面
  return extractStringField(buf, lastBraceIdx, 'message');
}
