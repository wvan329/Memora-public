<template>
  <Teleport to="body">
    <div
      v-if="visible"
      class="fixed inset-0 z-[9999] bg-black/90 select-none"
      @click.self="close"
    >
      <!-- 关闭按钮 -->
      <button
        class="absolute top-4 right-4 z-10 w-10 h-10 rounded-full bg-white/20 hover:bg-white/40 text-white text-2xl flex items-center justify-center transition-colors"
        @click="close"
        title="关闭 (Esc)"
      >✕</button>

      <!-- 图片计数 -->
      <div
        v-if="images.length > 1"
        class="absolute top-4 left-4 z-10 px-3 py-1.5 rounded-full bg-white/20 text-white text-sm"
      >{{ currentIndex + 1 }} / {{ images.length }}</div>

      <!-- 图片容器：铺满全屏，支持缩放和拖拽，支持滑动切换 -->
      <div
        ref="containerRef"
        class="absolute inset-0 flex items-center justify-center overflow-hidden"
        :style="{ cursor: scale > 1 ? (dragging ? 'grabbing' : 'grab') : 'default' }"
        @wheel.prevent="onWheel"
        @mousedown="onMouseDown"
        @mousemove="onMouseMove"
        @mouseup="onMouseUp"
        @mouseleave="onMouseUp"
        @touchstart.prevent="onTouchStart"
        @touchmove.prevent="onTouchMove"
        @touchend="onTouchEnd"
      >
        <img
          :key="currentIndex"
          :src="currentImage.url"
          :alt="currentImage.filename || ''"
          class="max-w-full max-h-full object-contain"
          :style="imgStyle"
          draggable="false"
          @dblclick.prevent="onDoubleClick"
          @load="resetTransform"
        />
      </div>

      <!-- 底部缩略图导航（多图时） -->
      <div
        v-if="images.length > 1"
        class="absolute bottom-4 left-1/2 -translate-x-1/2 z-10 flex gap-2"
      >
        <button
          v-for="(img, i) in images"
          :key="i"
          class="w-2.5 h-2.5 rounded-full transition-all"
          :class="i === currentIndex ? 'bg-white scale-125' : 'bg-white/40 hover:bg-white/70'"
          @click="goTo(i)"
          :title="img.filename || `图片 ${i + 1}`"
        />
      </div>
    </div>
  </Teleport>
</template>

<script setup lang="ts">
import { ref, computed, watch, onMounted, onUnmounted } from 'vue'

export interface ImageItem {
  url: string
  filename: string
}

const props = defineProps<{
  images: ImageItem[]
  initialIndex: number
  visible: boolean
}>()

const emit = defineEmits<{
  close: []
}>()

const containerRef = ref<HTMLElement | null>(null)

// 缩放
const scale = ref(1)
const MIN_SCALE = 0.5
const MAX_SCALE = 8
const ZOOM_STEP = 0.25

// 拖拽偏移
const offsetX = ref(0)
const offsetY = ref(0)
const dragging = ref(false)
let dragStartX = 0
let dragStartY = 0
let dragOffsetStartX = 0
let dragOffsetStartY = 0

// 触摸双指缩放
let lastPinchDist = 0
let pinchStartScale = 1

// 滑动切换
const SWIPE_THRESHOLD = 80
let swipeStartX = 0
let swipeStartY = 0
let isSwiping = false

const currentIndex = ref(props.initialIndex)

const currentImage = computed(() => props.images[currentIndex.value] || { url: '', filename: '' })

const imgStyle = computed(() => ({
  transform: `translate(${offsetX.value}px, ${offsetY.value}px) scale(${scale.value})`,
}))

function resetTransform() {
  scale.value = 1
  offsetX.value = 0
  offsetY.value = 0
}

function close() {
  resetTransform()
  emit('close')
}

function prev() {
  if (currentIndex.value > 0) {
    currentIndex.value--
    resetTransform()
  }
}

function next() {
  if (currentIndex.value < props.images.length - 1) {
    currentIndex.value++
    resetTransform()
  }
}

function goTo(i: number) {
  if (i >= 0 && i < props.images.length) {
    currentIndex.value = i
    resetTransform()
  }
}

// 键盘导航
function onKeydown(e: KeyboardEvent) {
  if (!props.visible) return
  if (e.key === 'Escape') close()
  if (e.key === 'ArrowLeft') prev()
  if (e.key === 'ArrowRight') next()
}

onMounted(() => window.addEventListener('keydown', onKeydown))
onUnmounted(() => window.removeEventListener('keydown', onKeydown))

watch(() => props.visible, (v) => {
  if (v) {
    currentIndex.value = props.initialIndex
    resetTransform()
    document.body.style.overflow = 'hidden'
  } else {
    document.body.style.overflow = ''
  }
})

watch(() => props.initialIndex, (v) => {
  if (props.visible) currentIndex.value = v
})

// ── 滚轮缩放：以鼠标位置为中心 ──
function onWheel(e: WheelEvent) {
  const delta = e.deltaY > 0 ? -ZOOM_STEP : ZOOM_STEP
  const oldScale = scale.value
  const newScale = Math.min(MAX_SCALE, Math.max(MIN_SCALE, oldScale + delta))

  const rect = containerRef.value?.getBoundingClientRect()
  if (rect) {
    const cx = e.clientX - rect.left - rect.width / 2
    const cy = e.clientY - rect.top - rect.height / 2
    const ratio = newScale / oldScale
    offsetX.value = offsetX.value * ratio + cx * (1 - ratio)
    offsetY.value = offsetY.value * ratio + cy * (1 - ratio)
  }

  scale.value = newScale
  if (newScale <= 1) {
    offsetX.value = 0
    offsetY.value = 0
  }
}

// ── 双击缩放 ──
function onDoubleClick() {
  if (scale.value > 1) {
    resetTransform()
  } else {
    scale.value = 3
  }
}

// ── 鼠标拖拽 ──
function onMouseDown(e: MouseEvent) {
  if (scale.value <= 1) return
  if (e.button !== 0) return
  dragging.value = true
  dragStartX = e.clientX
  dragStartY = e.clientY
  dragOffsetStartX = offsetX.value
  dragOffsetStartY = offsetY.value
}

function onMouseMove(e: MouseEvent) {
  if (!dragging.value) return
  offsetX.value = dragOffsetStartX + (e.clientX - dragStartX)
  offsetY.value = dragOffsetStartY + (e.clientY - dragStartY)
}

function onMouseUp() {
  dragging.value = false
}

// ── 触摸手势 ──
function getTouchDist(touches: TouchList): number {
  if (touches.length < 2) return 0
  const dx = touches[0].clientX - touches[1].clientX
  const dy = touches[0].clientY - touches[1].clientY
  return Math.hypot(dx, dy)
}

let touchStartX = 0
let touchStartY = 0

function onTouchStart(e: TouchEvent) {
  if (e.touches.length === 2) {
    /* 双指缩放 */
    lastPinchDist = getTouchDist(e.touches)
    pinchStartScale = scale.value
    isSwiping = false
    return
  }
  if (e.touches.length === 1) {
    touchStartX = e.touches[0].clientX
    touchStartY = e.touches[0].clientY
    if (scale.value > 1) {
      /* 放大后单指拖拽 */
      dragging.value = true
      isSwiping = false
      dragOffsetStartX = offsetX.value
      dragOffsetStartY = offsetY.value
    } else {
      /* scale=1 时可能是滑动切换 */
      isSwiping = true
      swipeStartX = e.touches[0].clientX
      swipeStartY = e.touches[0].clientY
    }
  }
}

function onTouchMove(e: TouchEvent) {
  if (e.touches.length === 2) {
    /* 双指缩放 */
    const dist = getTouchDist(e.touches)
    if (lastPinchDist > 0) {
      const ratio = dist / lastPinchDist
      scale.value = Math.min(MAX_SCALE, Math.max(MIN_SCALE, pinchStartScale * ratio))
    }
    return
  }
  if (e.touches.length === 1) {
    if (dragging.value) {
      /* 放大后拖拽 */
      offsetX.value = dragOffsetStartX + (e.touches[0].clientX - touchStartX)
      offsetY.value = dragOffsetStartY + (e.touches[0].clientY - touchStartY)
    } else if (isSwiping) {
      /* 滑动切换：仅当垂直移动小于水平时才视为滑动 */
      const dx = e.touches[0].clientX - swipeStartX
      const dy = e.touches[0].clientY - swipeStartY
      if (Math.abs(dy) > Math.abs(dx) * 1.2) {
        isSwiping = false
      }
      /* 不在这里切换，在 touchend 判断 */
    }
  }
}

function onTouchEnd(e: TouchEvent) {
  if (dragging.value) {
    dragging.value = false
    if (scale.value <= 1) {
      offsetX.value = 0
      offsetY.value = 0
    }
    return
  }
  if (isSwiping && scale.value <= 1 && e.changedTouches.length === 1) {
    const dx = e.changedTouches[0].clientX - swipeStartX
    if (Math.abs(dx) > SWIPE_THRESHOLD) {
      if (dx < 0) next()
      else prev()
    }
    isSwiping = false
  }
  lastPinchDist = 0
}
</script>
