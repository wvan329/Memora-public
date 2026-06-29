// ========================= Chat Store 核心流式逻辑测试 =========================
import { describe, it, expect, beforeEach } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'
import { useChatStore } from '../chat'

beforeEach(() => {
  setActivePinia(createPinia())
})

describe('useChatStore', () => {
  it('初始状态为空', () => {
    const store = useChatStore()
    expect(store.messages).toHaveLength(0)
    expect(store.isStreaming).toBe(false)
  })

  it('addUserMessage', () => {
    const store = useChatStore()
    store.addUserMessage('你好')
    expect(store.messages).toHaveLength(1)
    expect(store.messages[0].content).toBe('你好')
  })

  it('乐观消息确认', () => {
    const store = useChatStore()
    store.addUserMessage('你好', true)
    store.handleUserMessage('你好', 'turn-1')
    expect(store.messages[0].isOptimistic).toBe(false)
    expect(store.messages[0].turnId).toBe('turn-1')
  })

  it('其他客户端消息新增', () => {
    const store = useChatStore()
    store.handleUserMessage('手机消息', 'turn-2')
    expect(store.messages).toHaveLength(1)
    expect(store.messages[0].content).toBe('手机消息')
  })

  it('相同 turnId 不重复添加', () => {
    const store = useChatStore()
    store.handleUserMessage('msg1', 'turn-3')
    store.handleUserMessage('msg1 again', 'turn-3')
    expect(store.messages).toHaveLength(1)
  })

  // ====== 流式 ======

  it('startAIMessage 创建空流式消息', () => {
    const store = useChatStore()
    store.startAIMessage()
    expect(store.messages[0].role).toBe('ai')
    expect(store.messages[0].isStreaming).toBe(true)
    expect(store.isStreaming).toBe(true)
  })

  it('processChunk text 追加', () => {
    const store = useChatStore()
    store.startAIMessage()
    store.processChunk({ type: 'text', content: 'A' })
    store.processChunk({ type: 'text', content: 'B' })
    expect(store.messages[0].content).toBe('AB')
  })

  it('processChunk reason 创建块', () => {
    const store = useChatStore()
    store.startAIMessage()
    store.processChunk({ type: 'reason', content: 'think...' })
    expect(store.messages[0].thinkBlocks).toHaveLength(1)
    expect(store.messages[0].thinkBlocks[0].type).toBe('reasoning')
  })

  it('连续 reason 追加到同一块', () => {
    const store = useChatStore()
    store.startAIMessage()
    store.processChunk({ type: 'reason', content: 'A' })
    store.processChunk({ type: 'reason', content: 'B' })
    expect(store.messages[0].thinkBlocks).toHaveLength(1)
    const block = store.messages[0].thinkBlocks[0]
    expect(block.type).toBe('reasoning')
    if (block.type === 'reasoning') expect(block.text).toBe('AB')
  })

  it('thinkBlocks 保持交错顺序', () => {
    const store = useChatStore()
    store.startAIMessage()
    store.processChunk({ type: 'reason', content: 'R1' })
    store.processChunk({ type: 'tool_call_name', tool_call_id: 't1', tool_call_index: 0, content: 'fn' })
    store.processChunk({ type: 'reason', content: 'R2' })
    store.processChunk({ type: 'tool_call_name', tool_call_id: 't2', tool_call_index: 1, content: 'fn2' })
    const b = store.messages[0].thinkBlocks
    expect(b[0].type).toBe('reasoning')
    expect(b[1].type).toBe('tool')
    expect(b[2].type).toBe('reasoning')
    expect(b[3].type).toBe('tool')
  })

  it('finishStreaming 保留内容', () => {
    const store = useChatStore()
    store.startAIMessage()
    store.processChunk({ type: 'text', content: 'done' })
    store.finishStreaming()
    expect(store.isStreaming).toBe(false)
    expect(store.messages[0].content).toBe('done')
  })

  it('abortStreaming 保留有内容的 AI 消息', () => {
    const store = useChatStore()
    store.startAIMessage()
    store.processChunk({ type: 'text', content: 'partial' })
    store.abortStreaming('')
    expect(store.messages).toHaveLength(1)
    expect(store.messages[0].content).toBe('partial')
  })

  it('abortStreaming 移除空消息', () => {
    const store = useChatStore()
    store.startAIMessage()
    store.abortStreaming('')
    expect(store.messages).toHaveLength(0)
  })

  it('newSession 清空', () => {
    const store = useChatStore()
    store.addUserMessage('x')
    store.newSession()
    expect(store.messages).toHaveLength(0)
    expect(store.isStreaming).toBe(false)
  })

  it('pauseChunk 和 flushPausedChunks', () => {
    const store = useChatStore()
    store.startAIMessage()
    store.pauseChunk({ type: 'text', content: 'A' })
    store.pauseChunk({ type: 'text', content: 'B' })
    store.flushPausedChunks()
    expect(store.pausedChunks).toHaveLength(0)
    expect(store.messages[0].content).toBe('AB')
  })

  it('resumeStreamIfIncomplete 标记不完整消息', () => {
    const store = useChatStore()
    store.messages = [{
      id: 'm1', role: 'ai', content: '',
      thinkBlocks: [{ type: 'reasoning', text: '...' }],
      isStreaming: false,
    }] as any
    store.resumeStreamIfIncomplete()
    expect(store.messages[0].isStreaming).toBe(true)
    expect(store.isStreaming).toBe(true) // v4.5: 全局标记需要为 true 以让发送按钮变红
  })

  it('onChunk 回调触发', () => {
    const store = useChatStore()
    let n = 0
    store.onChunk(() => n++)
    store.startAIMessage()
    store.processChunk({ type: 'text', content: 'x' })
    expect(n).toBeGreaterThan(0)
  })
})
