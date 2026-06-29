<template>
  <Teleport to="body">
    <div v-if="entry" ref="dialogRoot" v-show="entry.visible" :data-dialog-id="dialogId" class="fixed inset-0 z-[9999] flex items-center justify-center bg-black/50" @click.self="minimize()">
      <div class="bg-white dark:bg-gray-900 rounded-xl p-2 w-[660px] max-w-[92vw] flex flex-col shadow-2xl overflow-hidden"
        :class="isLargeDialog ? 'h-[750px] max-h-[95vh]' : 'h-auto max-h-[85vh]'">


        <!-- 多页指示器 -->
        <div v-if="pages.length > 1" class="flex items-center justify-center gap-1.5 mb-1 flex-shrink-0 px-1">
          <span v-for="(_p, i) in pages" :key="i"
            class="rounded-full transition-all duration-200 cursor-pointer"
            :class="[i === curPage ? 'bg-blue-800 w-4 h-2' : 'bg-gray-300 dark:bg-gray-600 w-2 h-2']"
            @click="curPage = i">
          </span>
          <span class="text-[11px] text-gray-400 dark:text-gray-500 ml-1.5">{{ curPage + 1 }}/{{ pages.length }}</span>
        </div>

        <!-- 流式加载指示器（独立于内容，不受 message 覆盖影响） -->
        <div v-if="entry?.streaming && !entry?.resolved" class="flex items-center justify-center gap-2 mb-1 flex-shrink-0 text-xs text-gray-400 dark:text-gray-500">
          <span class="inline-block w-3.5 h-3.5 border-2 border-gray-300 border-t-blue-500 rounded-full animate-spin"></span>
          <span>处理中…</span>
        </div>

        <!-- 多页：堆叠滑动（灰色内容区） -->
        <div v-if="pages.length > 1" class="flex-1 min-h-0 relative overflow-x-hidden rounded-lg"
          @touchstart="ts" @touchmove="tm" @touchend="te"
          @mousedown="md">

          <!-- 滑动轨道 -->
          <div class="relative h-full z-10 overflow-hidden"
            :style="{ perspective: '800px' }">
            <div class="flex h-full"
              :style="trackStyle">
              <div v-for="(page, i) in pages" :key="i"
                :data-page-index="i"
                class="w-full flex-shrink-0 h-full overflow-y-auto overflow-x-hidden scrollbar-thin px-1"
                :style="cardStyle(i)"
                @wheel="(e: WheelEvent) => onPageWheel(e, i)"
                @scroll="(e: Event) => onPageScroll(e, i)">
                <div class="msg-content text-sm text-gray-800 dark:text-gray-200 mb-1.5 leading-relaxed" v-html="renderMarkdown(page.message, auth.password)" @click="onDialogImageClick"></div>
                <div v-if="page.options?.length" class="flex flex-col gap-1 mb-1.5">
                  <button v-for="(opt, j) in page.options" :key="j" @click="toggleOption(i, opt)"
                    class="w-full px-2.5 py-1.5 text-left border rounded-lg text-sm transition flex items-center gap-2"
                    :class="(selections[i] || []).includes(opt) ? 'bg-gray-100 dark:bg-gray-700 border-gray-900 dark:border-gray-400 text-gray-900 dark:text-gray-200' : 'bg-gray-50 dark:bg-gray-800 border-gray-200 dark:border-gray-600 text-gray-700 dark:text-gray-200 hover:bg-gray-100 dark:hover:bg-gray-700'">
                    <!-- 复选框图标（resolveOnSelect 模式不显示） -->
                    <span v-if="!entry?.state.resolveOnSelect" class="flex-shrink-0 w-4 h-4 border rounded text-[10px] flex items-center justify-center"
                      :class="(selections[i] || []).includes(opt) ? 'border-blue-500 bg-blue-800 text-white' : 'border-gray-300 dark:border-gray-500'">
                      <template v-if="(selections[i] || []).includes(opt)">✓</template>
                    </span>
                    <span>{{ opt }}</span>
                  </button>
                </div>
                <textarea v-if="currentShowInput && i === curPage" v-model="inputs[i]" rows="2"
                  placeholder="或输入自定义内容…"
                  style="overflow-y:hidden"
                  @input="autoResizeTextarea"
                  class="w-full px-2.5 py-1.5 border border-gray-300 dark:border-gray-600 rounded-lg text-sm resize-none
                         bg-white dark:bg-gray-800 text-gray-700 dark:text-gray-200
                         focus:border-gray-900 dark:focus:border-gray-400 outline-none transition
                         max-h-96 scrollbar-none"></textarea>
              </div>
            </div>
          </div>
        </div>

        <!-- 单页内容（灰色内容区） -->
        <div v-else class="flex-1 min-h-0 overflow-y-auto overflow-x-hidden scrollbar-thin rounded-lg p-2"
          @wheel="(e: WheelEvent) => onPageWheel(e, 0)"
          @scroll="(e: Event) => onPageScroll(e, 0)">
          <div class="msg-content text-sm text-gray-800 dark:text-gray-200 mb-1.5 leading-relaxed" v-html="renderMarkdown(currentPage.message, auth.password)" @click="onDialogImageClick"></div>
          <div v-if="currentPage.options?.length" class="flex flex-col gap-1 mb-1.5">
            <button v-for="(opt, i) in currentPage.options" :key="i" @click="toggleOption(curPage, opt)"
              class="w-full px-2.5 py-1.5 text-left border rounded-lg text-sm transition flex items-center gap-2"
              :class="(selections[curPage] || []).includes(opt) ? 'bg-gray-100 dark:bg-gray-700 border-gray-900 dark:border-gray-400 text-gray-900 dark:text-gray-200' : 'bg-gray-50 dark:bg-gray-800 border-gray-200 dark:border-gray-600 text-gray-700 dark:text-gray-200 hover:bg-gray-100 dark:hover:bg-gray-700'">
              <!-- 复选框图标（resolveOnSelect 模式不显示） -->
              <span v-if="!entry?.state.resolveOnSelect" class="flex-shrink-0 w-4 h-4 border rounded text-[10px] flex items-center justify-center"
                :class="(selections[curPage] || []).includes(opt) ? 'border-gray-900 dark:border-gray-400 bg-gray-900 dark:bg-gray-600 text-white' : 'border-gray-300 dark:border-gray-500'">
                <template v-if="(selections[curPage] || []).includes(opt)">✓</template>
              </span>
              <span>{{ opt }}</span>
            </button>
          </div>
          <textarea v-if="currentShowInput" v-model="inputs[curPage]" rows="2"
            placeholder="或输入自定义内容…"
            style="overflow-y:hidden"
            @input="autoResizeTextarea"
            class="w-full px-2.5 py-1.5 border border-gray-300 dark:border-gray-600 rounded-lg text-sm resize-none
                   bg-white dark:bg-gray-800 text-gray-700 dark:text-gray-200
                   focus:border-gray-900 dark:focus:border-gray-400 outline-none transition
                   max-h-96 scrollbar-none"></textarea>
        </div>

        <!-- 底部按钮：hideActions 时完全隐藏（如三点导航弹窗） -->
        <div v-if="!entry.resolved && !entry.state.hideActions" class="flex items-center justify-between flex-shrink-0 pt-1.5 border-t border-gray-100 dark:border-gray-800 mt-1">
          <button @click="resolve(null)"
            class="px-2.5 py-1 border border-gray-300 dark:border-gray-600 rounded-lg bg-gray-50 dark:bg-gray-800 text-xs text-gray-500 dark:text-gray-400 hover:bg-gray-100 dark:hover:bg-gray-700 transition">
            {{ entry.state.cancelText || '取消' }}
          </button>
          <button v-if="isLastPage" @click="resolve(buildResult())"
            :disabled="!canConfirm"
            class="px-3 py-1 rounded-lg text-xs transition text-white"
            :class="canConfirm ? 'bg-gray-900 hover:bg-black dark:bg-gray-600 dark:hover:bg-gray-500' : 'bg-gray-300 dark:bg-gray-600 text-gray-400 dark:text-gray-500 cursor-not-allowed'">
            {{ entry.state.confirmText || '确认' }}
          </button>
          <span v-else class="text-[11px] text-gray-400 dark:text-gray-500">← 滑动翻页 →</span>
        </div>
      </div>
    </div>
  </Teleport>
  <!-- 图片全屏查看器（弹窗内图片点击放大） -->
  <ImageViewer :images="previewImages" :initial-index="previewIndex" :visible="previewVisible" @close="previewVisible = false" />
</template>

<script setup lang="ts">
import { ref, watch, computed, reactive, nextTick, onUnmounted } from 'vue'
import { useUIStore } from '@/stores/ui'
import { useAuthStore } from '@/stores/auth'
import { renderMarkdown } from '@/utils/markdown'
import { useScrollContainer } from '@/composables/useScrollContainer'
import ImageViewer from '@/components/chat/ImageViewer.vue'
import type { ImageItem } from '@/components/chat/ImageViewer.vue'
import type { DialogPage, DialogEntry } from '@/types/ui'

const props = defineProps<{ dialogId: string }>()

const ui = useUIStore()
const auth = useAuthStore()

const dialogRoot = ref<HTMLElement | null>(null)

// ── 图片预览（点击弹窗内 <img> 打开全屏查看器）──
const previewVisible = ref(false)
const previewImages = ref<ImageItem[]>([])
const previewIndex = ref(0)

/** 收集弹窗内容区所有图片 URL，以当前点击的为初始索引打开预览 */
function onDialogImageClick(e: MouseEvent) {
  const target = e.target as HTMLElement
  if (target.tagName !== 'IMG') return
  const url = target.getAttribute('data-full-url') || target.getAttribute('src')
  if (!url || url.startsWith('data:')) return
  const contentEl = e.currentTarget as HTMLElement
  if (!contentEl) return
  const allImgs = contentEl.querySelectorAll('img')
  const images: ImageItem[] = []
  let clickedIdx = 0
  allImgs.forEach((img, idx) => {
    const u = img.getAttribute('data-full-url') || img.getAttribute('src') || ''
    if (u && !u.startsWith('data:')) {
      if (u === url) clickedIdx = images.length
      images.push({ url: u, filename: img.alt || '' })
    }
  })
  if (images.length === 0) return
  previewImages.value = images
  previewIndex.value = clickedIdx
  previewVisible.value = true
}

// 每个组件实例独立的状态
// selections 统一用 string[] 存储：单选时数组最多 1 个元素，多选时可多个
const selections = reactive<Record<number, string[]>>({})
const inputs = reactive<Record<number, string>>({})
const curPage = ref(0)
const dragX = ref(0)
const settling = ref(false)
let startX = 0; let startY = 0; let dragging = false
let _activeMm: ((e: MouseEvent) => void) | null = null
let _activeMu: (() => void) | null = null
let scrollableEl: HTMLElement | null = null   // 触摸所在的可横向滚动元素
let scrollableAtEdge = false                   // 已到达边界并开始翻页

// ═══════════════════════════════════════════════════════════════
// 自动滚动：复用 useScrollContainer composable
// ═══════════════════════════════════════════════════════════════
const { autoScrollFlags: pageAutoScroll, scrollPageToBottom: _compScrollToBottom, onPageScroll, onPageWheel, setAutoScroll } = useScrollContainer()

// 从 store 中查找当前弹窗实例
const entry = computed<DialogEntry | undefined>(() =>
  ui.dialogs.find(e => e.id === props.dialogId)
)

const pages = computed(() => entry.value?.state.pages || [])
// pages 为空时（单页模式），回退到 state 的 message/options 字段
const currentPage = computed<DialogPage>(() => pages.value[curPage.value] || {
  message: entry.value?.state.message || '',
  options: entry.value?.state.options || [],
  confirm_text: entry.value?.state.confirmText,
  cancel_text: entry.value?.state.cancelText,
})
const isLastPage = computed(() => pages.value.length <= 1 || curPage.value >= pages.value.length - 1)
const currentShowInput = computed(() => entry.value?.state.showInput !== false)
// 大弹窗：多页 / 有选项列表 / 流式交互弹窗 → 固定 750px
// 小弹窗：简单确认框 → 高度自适应内容
const isLargeDialog = computed(() =>
  pages.value.length > 1 ||
  (currentPage.value.options?.length ?? 0) > 0 ||
  (entry.value?.streaming ?? false)
)

// 同步 curPage 到 state
watch(curPage, (v) => {
  if (entry.value) entry.value.state.currentPageIndex = v
})
watch(() => entry.value?.state.currentPageIndex, (v) => {
  if (v !== undefined) curPage.value = v
})

// 弹窗从隐藏变为可见时，重置状态（immediate 确保首次挂载时也初始化 inputValue）
watch(() => entry.value?.visible, (val) => {
  if (val) {
    Object.keys(selections).forEach(k => { selections[Number(k)] = [] })
    Object.keys(inputs).forEach(k => delete inputs[Number(k)])
    // 预填输入框初始值
    if (entry.value?.state.inputValue) {
      inputs[0] = entry.value.state.inputValue
    }
    curPage.value = entry.value?.state.currentPageIndex || 0
    dragX.value = 0; dragging = false; settling.value = false
    // 重显时默认开启所有页面的自动滚动
    for (let i = 0; i < (pages.value.length || 1); i++) {
      pageAutoScroll[i] = true
    }
    // 重置所有 textarea 高度：上一次 autoResizeTextarea 写入的 style.height
    // 在 v-show 隐藏期间不会自动清除，导致空内容时仍撑高
    nextTick(() => {
      if (dialogRoot.value) {
        dialogRoot.value.querySelectorAll('textarea').forEach(el => {
          (el as HTMLTextAreaElement).style.height = 'auto'
        })
      }
      // 弹窗打开后自动滚动到当前页底部；
      // 用 rAF 确保 flex 容器完成布局、scrollHeight 已稳定。
      requestAnimationFrame(() => { doScrollPageToBottom(curPage.value) })
    })
  }
}, { immediate: true })

// 当前页 message 变化 → 自动滚到底部（仅当该页开启了自动滚动）
watch(() => pages.value[curPage.value]?.message, () => {
  nextTick(() => {
    // 确保该页 autoScroll 有值（首次默认开）
    if (pageAutoScroll[curPage.value] === undefined) {
      pageAutoScroll[curPage.value] = true
    }
    doScrollPageToBottom(curPage.value)
  })
})

// 切换页面后，滚动到新页面的底部（如果开启了自动滚动）
watch(curPage, (newPage) => {
  nextTick(() => {
    if (pageAutoScroll[newPage] === undefined) {
      pageAutoScroll[newPage] = true
    }
    doScrollPageToBottom(newPage)
  })
})

// 参考 InputArea.vue 的 autoResize：输入时自动增高，达到 max-h 后显示滚动条
function autoResizeTextarea(e: Event) {
  const el = e.target as HTMLTextAreaElement
  el.style.height = 'auto'
  const newH = Math.min(el.scrollHeight, 384) // 384px = max-h-96
  el.style.height = newH + 'px'
  el.style.overflowY = el.scrollHeight > 384 ? 'auto' : 'hidden'
}

function toggleOption(pageIdx: number, opt: string) {
  const cur = selections[pageIdx] || []
  // 所有选项默许多选：toggle 模式
  const idx = cur.indexOf(opt)
  if (idx >= 0) cur.splice(idx, 1)
  else cur.push(opt)
  selections[pageIdx] = [...cur]

  // 如果设置了 resolveOnSelect，点击选项立即提交（如三点导航弹窗）
  if (entry.value?.state.resolveOnSelect) {
    resolve({ selected: opt, text: inputs[pageIdx]?.trim() || undefined })
  }
}

// 是否可以确认
const canConfirm = computed(() => {
  if (entry.value?.streaming) return false
  if (pages.value.length <= 1) {
    const page = currentPage.value
    if (!page.options?.length) return true
    // requireOptions=false 时，不强制选选项即可确认
    if (entry.value?.state.requireOptions === false) return true
    const sel = selections[curPage.value]
    return (sel && sel.length > 0) || !!inputs[curPage.value]?.trim()
  }
  return pages.value.every((page, i) => {
    if (!page.options?.length) return true
    if (entry.value?.state.requireOptions === false) return true
    const sel = selections[i]
    return (sel && sel.length > 0) || !!inputs[i]?.trim()
  })
})

// ── 轨道位置 ──
const trackStyle = computed(() => {
  const dragPct = dragX.value / 4
  const pct = -curPage.value * 100 + dragPct
  return {
    transform: `translateX(${pct}%)`,
    transition: dragging ? 'none' : 'transform 0.4s cubic-bezier(0.25,0.8,0.25,1)',
  }
})

// ── 每张卡片 ──
function cardStyle(i: number) {
  const delta = i - curPage.value
  if (Math.abs(delta) > 1) return { opacity: 0, pointerEvents: 'none' as const }
  const dragPct = dragX.value / 4
  const x = delta * 100 + dragPct
  const rotY = (dragging && delta === 0) ? dragX.value * 0.03 : 0
  return {
    transform: `translateX(${x}%) rotateY(${rotY}deg)`,
    opacity: Math.abs(delta) > 0.5 ? 0.6 : 1,
    transition: dragging ? 'none' : 'all 0.4s cubic-bezier(0.25,0.8,0.25,1)',
  }
}

// ── 手势 ──

// 向上查找目标元素所在的可横向滚动容器（如 markdown 代码块 pre）
// 返回元素引用，用于后续边界检测
function findScrollableX(el: HTMLElement): HTMLElement | null {
  let cur: HTMLElement | null = el
  while (cur && cur !== dialogRoot.value) {
    const s = window.getComputedStyle(cur)
    if ((s.overflowX === 'auto' || s.overflowX === 'scroll') && cur.scrollWidth > cur.clientWidth) {
      return cur
    }
    cur = cur.parentElement
  }
  return null
}

function ts(e: TouchEvent) {
  startX = e.touches[0].clientX; startY = e.touches[0].clientY
  dragging = true
  scrollableEl = findScrollableX(e.target as HTMLElement)
  scrollableAtEdge = false
}
function tm(e: TouchEvent) {
  if (!dragging) return
  const dx = e.touches[0].clientX - startX; const dy = e.touches[0].clientY - startY

  // 触摸在可横向滚动元素内：检查是否滚到边界
  // 未到边界 → 不翻页，让内部元素滚动；已到边界 → 重置起点开始翻页
  if (scrollableEl && !scrollableAtEdge) {
    if (Math.abs(dx) > Math.abs(dy) && Math.abs(dx) > 8) {
      const atLeftEdge = scrollableEl.scrollLeft <= 0
      const atRightEdge = scrollableEl.scrollLeft >= scrollableEl.scrollWidth - scrollableEl.clientWidth - 1
      const scrollingRight = dx > 0  // 手指向右 → 翻到上一页
      const scrollingLeft = dx < 0   // 手指向左 → 翻到下一页
      const canGoPrev = curPage.value > 0
      const canGoNext = !isLastPage.value

      if ((scrollingLeft && atRightEdge && canGoNext) || (scrollingRight && atLeftEdge && canGoPrev)) {
        // 到达边界 → 从此位置开始计算翻页手势
        scrollableAtEdge = true
        startX = e.touches[0].clientX
        startY = e.touches[0].clientY
        return
      }
    }
    // 内部元素还能滚动，不触发翻页
    if (Math.abs(dx) > Math.abs(dy)) return
  }

  // 原有翻页逻辑
  if (Math.abs(dx) > Math.abs(dy) && Math.abs(dx) > 8) {
    const atStart = curPage.value === 0 && dx > 0
    const atEnd = isLastPage.value && dx < 0
    dragX.value = atStart || atEnd ? dx * 0.25 : dx
  }
}
function te() {
  if (!dragging) return; dragging = false
  scrollableEl = null
  scrollableAtEdge = false
  if (dragX.value < -30 && !isLastPage.value) { settle('next') }
  else if (dragX.value > 30 && curPage.value > 0) { settle('prev') }
  else { dragX.value = 0 }
}

function md(e: MouseEvent) {
  const t = e.target as HTMLElement
  if (t.closest('button, textarea, input')) return
  // 桌面端：鼠标在可横向滚动元素内不启动拖拽翻页（用户可能在选文本）
  if (findScrollableX(t)) return
  startX = e.clientX; startY = e.clientY; dragging = true
  const mm = (ev: MouseEvent) => {
    if (!dragging) return
    const dx = ev.clientX - startX; const dy = ev.clientY - startY
    if (Math.abs(dx) > Math.abs(dy) && Math.abs(dx) > 8) {
      const atStart = curPage.value === 0 && dx > 0
      const atEnd = isLastPage.value && dx < 0
      dragX.value = atStart || atEnd ? dx * 0.25 : dx
    }
  }
  const mu = () => {
    document.removeEventListener('mousemove', mm); document.removeEventListener('mouseup', mu)
    _activeMm = null; _activeMu = null
    if (!dragging) return; dragging = false
    if (dragX.value < -30 && !isLastPage.value) { settle('next') }
    else if (dragX.value > 30 && curPage.value > 0) { settle('prev') }
    else { dragX.value = 0 }
  }
  _activeMm = mm; _activeMu = mu
  document.addEventListener('mousemove', mm); document.addEventListener('mouseup', mu)
}

// 组件卸载时清理可能残留的鼠标拖拽监听器
onUnmounted(() => {
  if (_activeMm) document.removeEventListener('mousemove', _activeMm)
  if (_activeMu) document.removeEventListener('mouseup', _activeMu)
  _activeMm = null
  _activeMu = null
  dragging = false
})

function settle(dir: 'next' | 'prev') {
  settling.value = true
  dragX.value = dir === 'next' ? -400 : 400
  setTimeout(() => {
    if (dir === 'next') curPage.value++
    else curPage.value--
    dragX.value = 0
    settling.value = false
  }, 100)
}

function buildResult(): Record<string, unknown> {
  // 多页模式（ask_user 弹窗）：每页一个答案
  if (pages.value.length > 0) {
    return {
      answers: pages.value.map((_p, i) => {
        const sel = selections[i]
        return {
          selected: sel && sel.length > 0 ? sel : undefined,
          text: inputs[i]?.trim() || undefined,
        }
      })
    }
  }
  // 单页模式（showDialog 直接创建的本地弹窗，如选图面板）：当前页一个答案
  const pageIdx = curPage.value
  const sel = selections[pageIdx]
  return {
    answers: [{
      selected: sel && sel.length > 0 ? sel : undefined,
      text: inputs[pageIdx]?.trim() || undefined,
    }]
  }
}

function minimize() {
  ui.minimizeDialog(props.dialogId)
}

// ── 自动滚动 ──

/** 获取指定页面的滚动容器元素（AppDialog 特有逻辑） */
function getPageScrollEl(pageIdx: number): HTMLElement | null {
  if (!dialogRoot.value) return null
  if (pages.value.length > 1) {
    return dialogRoot.value.querySelector(`[data-page-index="${pageIdx}"]`) as HTMLElement | null
  } else {
    return dialogRoot.value.querySelector('.scrollbar-thin') as HTMLElement | null
  }
}

/** scrollPageToBottom wrapper：注入 AppDialog 的 getPageScrollEl */
function doScrollPageToBottom(pageIdx: number) {
  _compScrollToBottom(pageIdx, getPageScrollEl);
}

function resolve(result: Record<string, unknown> | null) {
  const e = entry.value
  if (!e) return
  // 先取回调和 streamingCallback，然后关闭弹窗
  const cb = e.state.callback
  const streamingCb = e.streamingCallback
  ui.finalizeDialog(props.dialogId)
  ui.closeDialog(props.dialogId)
  // 触发回调
  if (streamingCb) streamingCb(result)
  else if (cb) cb(result)
}
</script>