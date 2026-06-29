<template>
  <footer class="bg-white dark:bg-gray-900 border-t border-gray-200 dark:border-gray-700 px-4 py-3 flex-shrink-0">
    <div class="max-w-5xl mx-auto flex items-end gap-2">
      <div class="flex-1 relative">
        <!-- 待发送图片缩略图预览 -->
        <div v-if="pendingImages.length > 0" class="flex gap-1.5 flex-wrap px-1 pb-1.5">
          <div v-for="(img, i) in pendingImages" :key="i" class="relative group">
            <img :src="img.objectURL" class="h-16 rounded-lg border border-gray-200 dark:border-gray-600 object-cover cursor-pointer" @click="openImagePreview(img, i)" />
            <button @click="removeImage(i)"
              class="absolute -top-1.5 -right-1.5 w-5 h-5 rounded-full bg-red-500 text-white text-xs flex items-center justify-center opacity-0 group-hover:opacity-100 transition hover:bg-red-600">
              ✕
            </button>
          </div>
        </div>
        <!-- 输入框 + 内置图片按钮（外层画边框，flex 并排） -->
        <div class="flex border border-gray-300 dark:border-gray-600 rounded-xl bg-white dark:bg-gray-800 transition focus-within:border-gray-900 dark:focus-within:border-gray-400">
          <textarea ref="textareaRef" v-model="inputText" rows="1"
            :placeholder="isStreaming ? '……' : '…'"
            class="flex-1 border-0 outline-none bg-transparent px-4 py-2.5 text-sm resize-none
                   text-gray-700 dark:text-gray-200 placeholder-gray-400 dark:placeholder-gray-500
                   scrollbar-none"
            style="overflow-y:hidden"
            @input="autoResize"
            @compositionstart="ui.isComposing = true"
            @compositionend="ui.isComposing = false"
            @blur="onTextareaBlur"
            @keydown="onKeydown"
            @keyup="onKeyup"
            @paste="onPaste">
          </textarea>
          <button @click="onPickImage" :disabled="isUploading"
            class="flex-shrink-0 self-center pr-2.5 text-gray-400 dark:text-gray-500 hover:text-gray-600 dark:hover:text-gray-300 transition select-none"
            title="选择图片">
            <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 16l4.586-4.586a2 2 0 012.828 0L16 16m-2-2l1.586-1.586a2 2 0 012.828 0L20 14m-6-6h.01M6 20h12a2 2 0 002-2V6a2 2 0 00-2-2H6a2 2 0 00-2 2v12a2 2 0 002 2z" />
            </svg>
          </button>
        </div>
      </div>
      <!--
        按钮布局：仅停止 / 发送，图片按钮已移入输入框左侧
        流式+有输入：红色停止 + 发送
        流式+无输入：仅发送（变红停止方块）
      -->
      <div class="flex flex-row items-center gap-1.5 flex-shrink-0">
        <!-- 流式输出 + 有输入时，左侧显示红色停止按钮 -->
        <button v-if="isStreaming && hasInput" @click="onStopClick"
          @mousedown="onBtnDown(true)" @mouseup="onBtnUp" @mouseleave="onBtnUp"
          @touchstart="onBtnDown(true)" @touchend="onBtnUp" @touchcancel="onBtnUp"
          class="bg-red-500 hover:bg-red-600 text-white rounded-xl w-10 h-10 flex items-center justify-center transition select-none"
          title="停止输出（长按1秒直接终止）">
          <svg class="w-5 h-5" fill="currentColor" viewBox="0 0 24 24">
            <rect x="5" y="5" width="14" height="14" rx="2" />
          </svg>
        </button>
        <!-- 主按钮：发送 / 停止（流式+无输入时变红）。
             长按：流式+无输入时直接终止，否则追加「只分析不执行」并发送。 -->
        <button @click="onClick"
          @mousedown="onBtnDown(isStreaming && !hasInput)" @mouseup="onBtnUp" @mouseleave="onBtnUp"
          @touchstart="onBtnDown(isStreaming && !hasInput)" @touchend="onBtnUp" @touchcancel="onBtnUp"
          :disabled="isUploading"
          class="text-white rounded-xl w-10 h-10 flex items-center justify-center flex-shrink-0 transition disabled:opacity-50 disabled:cursor-not-allowed select-none"
          :class="btnClass"
          :title="isUploading ? '图片上传中…' : btnTitle">
          <!-- 停止图标：仅流式+无输入（单按钮模式） -->
          <svg v-if="isStreaming && !hasInput" class="w-5 h-5" fill="currentColor" viewBox="0 0 24 24">
            <rect x="5" y="5" width="14" height="14" rx="2" />
          </svg>
          <!-- 发送图标：非流式，或流式但有输入 -->
          <svg v-else class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 19l9 2-9-18-9 18 9-2zm0 0v-8"/>
          </svg>
        </button>
      </div>
    </div>
  </footer>
  <!-- 输入框图片预览放大 -->
  <ImageViewer :images="previewImages" :initial-index="previewIndex" :visible="previewVisible" @close="previewVisible = false" />
</template>

<script setup lang="ts">
import { ref, computed, watch, nextTick } from 'vue'
import ImageViewer from '@/components/chat/ImageViewer.vue'
import type { ImageItem } from '@/components/chat/ImageViewer.vue'
import { useChatStore } from '@/stores/chat'
import { useWebSocketStore } from '@/stores/websocket'
import { useAuthStore } from '@/stores/auth'
import { useUIStore } from '@/stores/ui'
import { compressImage, openFilePicker, uploadBlobs } from '@/utils/vision'
import { STORAGE_KEYS } from '@/utils/storageKeys'

const chat = useChatStore()
const wsStore = useWebSocketStore()
const auth = useAuthStore()
const ui = useUIStore()

const inputText = ref('')
const textareaRef = ref<HTMLTextAreaElement | null>(null)
const pendingImages = ref<{ blob: Blob; objectURL: string }[]>([])
const isUploading = ref(false)
const isStreaming = computed(() => chat.isStreaming)

// ── 图片预览 ──
const previewVisible = ref(false)
const previewImages = ref<ImageItem[]>([])
const previewIndex = ref(0)

function openImagePreview(img: { objectURL: string }, idx: number) {
  previewImages.value = pendingImages.value.map(p => ({ url: p.objectURL, filename: '' }))
  previewIndex.value = idx
  previewVisible.value = true
}

// 监听聚焦请求：新会话等场景自动聚焦输入框
watch(() => ui.focusInputRequest, () => {
  nextTick(() => textareaRef.value?.focus())
})

// 监听丢弃的待发送消息 → 回显到输入框
watch(() => chat.discardedBufferMsg, (msg) => {
  if (msg) {
    inputText.value = msg
    chat.discardedBufferMsg = null
    nextTick(() => {
      autoResize()
      textareaRef.value?.focus()
    })
  }
})

const hasInput = computed(() => inputText.value.trim() !== '' || pendingImages.value.length > 0)

// 输入框失焦时短暂标记，阻止后续 click 触发空白区域刷新（手机端关闭输入法场景）
let blurTimer: ReturnType<typeof setTimeout> | null = null
function onTextareaBlur() {
  ui.isComposing = true
  if (blurTimer) clearTimeout(blurTimer)
  blurTimer = setTimeout(() => { ui.isComposing = false }, 200)
}

// ── 长按（1s）：停止按钮直接终止，发送按钮追加文字并发送 ──
const LONG_PRESS_MS = 1000
let longPressTimer: ReturnType<typeof setTimeout> | null = null
let longPressTriggered = false

function onBtnDown(isStopBtn = false) {
  longPressTriggered = false
  longPressTimer = setTimeout(() => {
    longPressTriggered = true
    if (isStopBtn) {
      // 停止按钮长按：直接终止，跳过确认弹窗
      doStop()
    } else {
      // 发送按钮长按：追加文字并发送（不管是否在流式中）
      if (!inputText.value.trim()) return
      inputText.value += '，分析一下，不要执行任何操作'
      send()
    }
  }, LONG_PRESS_MS)
}

function onBtnUp() {
  if (longPressTimer) {
    clearTimeout(longPressTimer)
    longPressTimer = null
  }
}

// localStorage key：只存最近发送的一条消息，每次发送覆盖，永不删除

// 按钮样式：两色——蓝色发送（有输入或非流式空输入），红色停止（流式+空输入）
// getStreamingMessage() 用于 onClick 双保险：isStreaming 可能因异步问题卡在 true
const streamingMsg = computed(() => chat.getStreamingMessage())
const btnClass = computed(() => {
  if (isStreaming.value && !hasInput.value) return 'bg-red-500 hover:bg-red-600'
  return 'bg-gray-900 hover:bg-black dark:bg-gray-800 dark:hover:bg-gray-600'
})

const btnTitle = computed(() => {
  if (isStreaming.value && !hasInput.value) return '停止（长按1秒直接终止）'
  return '发送'
})

function onClick() {
  if (longPressTriggered) {
    longPressTriggered = false  // 重置，否则后续点击全被跳过
    return
  }
  if (hasInput.value) send();
  else if (isStreaming.value && streamingMsg.value) stop();
  else restoreLastMessage();    // 无输入 + 非流式 → 加载最近消息到输入框
}

/** 专用停止按钮的 click：长按已由 onBtnDown 处理，短按走确认弹窗 */
function onStopClick() {
  if (longPressTriggered) {
    longPressTriggered = false
    return
  }
  stop()
}

async function send() {
  // 防止并发调用（上传中再按发送/Ctrl+Enter）
  if (isUploading.value) return;

  const prompt = inputText.value.trim();

  // 有图片：先上传（即使没有文字也上传）
  let imageUrls: string[] = []
  if (pendingImages.value.length > 0) {
    isUploading.value = true
    try {
      imageUrls = await uploadPendingImages()
    } catch (e) {
      // 上传失败不阻塞，继续发送文字
      console.error('图片上传失败:', e)
    } finally {
      isUploading.value = false
    }
  }

  if (!prompt && imageUrls.length === 0) return;

  // 拼图片 URL 到消息末尾
  let finalPrompt = prompt
  if (imageUrls.length > 0) {
    const imgPart = imageUrls.map(u => `[图片](${u})`).join('\n')
    finalPrompt = prompt ? prompt + '\n' + imgPart : imgPart
  }

  // 存入 localStorage，防止页面刷新/WebSocket 断连导致消息丢失。
  // 每次发送直接覆盖，永不删除——撤回按钮始终能恢复最近一条。
  // 注意：仅存纯文本部分，不含图片 URL
  localStorage.setItem(STORAGE_KEYS.LAST_SENT, prompt);

  // 引用文字在 handleQuote 中已通过 insertAtCursor 拼入 textarea，
  // 这里不再重复 buildQuotePrefix，避免双重拼接。
  chat.lastUserQuestion = finalPrompt;
  inputText.value = '';
  nextTick(() => autoResize());

  // 清理待发送图片
  pendingImages.value.forEach(img => URL.revokeObjectURL(img.objectURL))
  pendingImages.value = []

  // 先开自动滚动再发乐观消息——确保 _onChunk → scrollToBottom 时 autoScroll 已为 true。
  // addUserMessage 触发的 DOM 更新会同步派发 scroll 事件，onScroll 可能误关 autoScroll，
  // 所以提前设 programScrolling 压制，由后续 scrollToBottom 的 rAF 重置。
  ui.setAutoScroll(true);
  ui.programScrolling = true;

  if (isStreaming.value) {
    // 后端忙 → 不显示乐观气泡，改为底部待发送指示器
    chat.pendingBufferMsg = finalPrompt;
  } else {
    chat.addUserMessage(finalPrompt, true);
  }

  if (!wsStore.isConnected() && auth.password) {
    wsStore.connect();
  }

  wsStore.send({ type: 'chat', session_id: chat.sessionId, prompt: finalPrompt });
  // 立即标记流式状态，按钮直接变红，不等 user_message 回执
  chat.markStreaming();
}

/** 终止回复前弹出确认弹窗，防止误触 */
function stop() {
  ui.showDialog({
    message: '确认终止 AI 回复？\n已接收的内容将保留。',
    confirmText: '确认终止',
    cancelText: '继续等待',
    showInput: false,
    callback: (result: Record<string, unknown> | null) => {
      if (result !== null) {
        doStop();
      }
    },
  });
}

function doStop() {
  wsStore.send({ type: 'abort', session_id: chat.sessionId });
  if (chat.lastUserQuestion) {
    // 只在输入框为空时才恢复上次问题，避免覆盖用户已输入的内容
    if (!inputText.value.trim() && pendingImages.value.length === 0) {
      inputText.value = chat.lastUserQuestion;
    }
    chat.lastUserQuestion = '';
    autoResize();
  }
  chat.abortStreaming(inputText.value);
}

/** 将 localStorage 中保存的最近一条消息恢复到输入框（不删除备份） */
function restoreLastMessage() {
  const text = localStorage.getItem(STORAGE_KEYS.LAST_SENT);
  if (text) {
    inputText.value = text;
    nextTick(() => {
      autoResize();
      textareaRef.value?.focus();
    });
  }
}

// ── Enter 长按检测：1 秒后追加「只分析不执行」并发送 ──
let enterTimer: ReturnType<typeof setTimeout> | null = null
let enterLongPressFired = false

function onKeydown(e: KeyboardEvent) {
  // e.isComposing 是 DOM 标准属性，输入法激活时为 true，macOS/Windows 行为一致
  if (e.isComposing || isUploading.value) return

  // Shift+Enter 换行，Ctrl+Enter 不做特殊处理
  if (e.key === 'Enter' && !e.shiftKey && !e.ctrlKey && !e.metaKey) {
    e.preventDefault()
    // 已有定时器在跑或长按已触发 → 忽略键盘重复的 keydown
    if (enterTimer || enterLongPressFired) return
    enterLongPressFired = false
    enterTimer = setTimeout(() => {
      enterLongPressFired = true
      enterTimer = null
      if (!inputText.value.trim()) return
      inputText.value += '，分析一下，不要执行任何操作'
      send()
    }, LONG_PRESS_MS)
  }
}

function onKeyup(e: KeyboardEvent) {
  if (e.key === 'Enter') {
    // 短按：定时器还在 → 清除并正常发送
    if (enterTimer) {
      clearTimeout(enterTimer)
      enterTimer = null
      if (!enterLongPressFired && hasInput.value) {
        send()
      } else if (!enterLongPressFired && !hasInput.value) {
        restoreLastMessage()
      }
    }
    // 无论短按还是长按，松手时重置标志，确保下次 Enter 正常工作
    enterLongPressFired = false
  }
}

function autoResize() {
  const el = textareaRef.value;
  if (!el) return;
  el.style.height = 'auto';
  const newH = Math.min(el.scrollHeight, 128);
  el.style.height = newH + 'px';
  el.style.overflowY = el.scrollHeight > 128 ? 'auto' : 'hidden';
}

// ── 图片粘贴与上传 ──

/** 粘贴事件：将剪贴板中的图片压缩后加入待发送列表 */
function onPaste(e: ClipboardEvent) {
  const items = e.clipboardData?.items
  if (!items) return
  for (let i = 0; i < items.length; i++) {
    const item = items[i]
    if (item.type.startsWith('image/')) {
      e.preventDefault()  // 阻止图片粘贴到 textarea 变成 base64
      const file = item.getAsFile()
      if (!file) continue
      // 立即压缩（不解耦，固定参数：maxSize 1500, quality 0.7）
      compressToPending(file)
    }
  }
}

/** 压缩图片并加入待发送列表，参数跟随高精度开关 */
async function compressToPending(file: File) {
  const highRes = ui.visionHighRes
  const maxSize = highRes ? 2000 : 1300
  const quality = highRes ? 0.8 : 0.6
  const blob = await compressImage(file, maxSize, quality)
  const objectURL = URL.createObjectURL(blob)
  pendingImages.value = [...pendingImages.value, { blob, objectURL }]
}

/** 选择图片按钮 */
async function onPickImage() {
  const files = await openFilePicker()
  if (!files) return
  for (const file of files) {
    await compressToPending(file)
  }
}

/** 删除待发送图片 */
function removeImage(i: number) {
  URL.revokeObjectURL(pendingImages.value[i].objectURL)
  pendingImages.value = pendingImages.value.filter((_, idx) => idx !== i)
}

/** 上传所有待发送图片到云服务器，返回公网 URL 列表 */
async function uploadPendingImages(): Promise<string[]> {
  const blobs = pendingImages.value.map(p => p.blob)
  return uploadBlobs(blobs)
}
</script>