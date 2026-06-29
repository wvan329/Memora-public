<template>
  <aside id="sidebar"
    class="bg-white dark:bg-gray-900 border-r border-gray-200 dark:border-gray-700 flex flex-col flex-shrink-0 shadow-sm z-20 transition-[width] duration-250 ease-out overflow-hidden"
    :class="sidebarClass">
    <!-- 桌面折叠按钮 -->
    <div class="flex justify-end px-2 pt-2" v-if="!isMobile">
      <button @click="ui.toggleSidebar()"
        class="text-gray-400 dark:text-gray-500 hover:text-gray-600 dark:hover:text-gray-300 p-1 rounded transition"
        :title="ui.sidebarCollapsed ? '展开侧边栏' : '折叠侧边栏'">
        <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path v-if="ui.sidebarCollapsed" stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13 5l7 7-7 7M5 5l7 7-7 7"/>
          <path v-else stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M11 19l-7-7 7-7m8 14l-7-7 7-7"/>
        </svg>
      </button>
    </div>

    <div class="flex items-center justify-center gap-2 px-3 py-3 border-b border-gray-100 dark:border-gray-800">
      <img src="/favicon.png" class="w-5 h-5 flex-shrink-0" alt="Memora" />
      <span v-show="!ui.sidebarCollapsed || isMobile" class="font-semibold text-gray-700 dark:text-gray-200 text-sm">Memora</span>
    </div>

    <!-- 设备切换：仅 Android 端显示 -->
    <div v-if="isAndroid" v-show="!ui.sidebarCollapsed || isMobile" class="px-3 py-2 border-b border-gray-100 dark:border-gray-800 sidebar-full">
      <div class="flex gap-1">
        <button v-for="d in devices" :key="d.key"
          @click="switchDevice(d.key)"
          class="flex-1 text-xs py-1.5 rounded-md transition font-medium"
          :class="currentDevice === d.key
            ? 'bg-gray-900 dark:bg-gray-600 text-white shadow-sm'
            : 'bg-gray-100 dark:bg-gray-800 text-gray-500 dark:text-gray-400 hover:bg-gray-200 dark:hover:bg-gray-700'">
          {{ d.icon }} {{ d.label }}
        </button>
      </div>
    </div>

    <div class="px-3 py-2 border-b border-gray-100 dark:border-gray-800 sidebar-full">
      <button @click="handleNewSession"
        class="bg-gray-900 hover:bg-black dark:bg-gray-600 dark:hover:bg-gray-500 text-white text-xs font-medium rounded-lg transition w-full text-center"
        :class="ui.sidebarCollapsed && !isMobile ? 'px-0 py-1.5' : 'px-3 py-1.5'">
        {{ ui.sidebarCollapsed && !isMobile ? '＋' : '＋ 新会话' }}
      </button>
    </div>

    <SessionList v-show="!ui.sidebarCollapsed || isMobile"
      :current-id="chat.sessionId"
      :loading="sessions.loading" :error="sessions.error"
      :collapsed="ui.sidebarCollapsed && !isMobile"
      @select="handleSelect" @delete="handleDelete" />

    <!-- 自动滚动按钮已隐藏 -->
    <!-- <AutoScrollToggle :auto-scroll="ui.autoScroll" :collapsed="ui.sidebarCollapsed && !isMobile"
      @toggle="ui.setAutoScroll(!ui.autoScroll)" /> -->

    <!-- 设置入口：仅侧边栏展开时可见 -->
    <div v-show="!ui.sidebarCollapsed || isMobile" class="px-3 py-2 border-t border-gray-100 dark:border-gray-800 relative">
      <!-- 设置面板：向上展开，绝对定位脱离文档流，按钮保持不动 -->
      <div v-if="showSettings"
        class="absolute bottom-full left-0 right-0 mb-2 mx-3 bg-white dark:bg-gray-800 rounded-lg shadow-sm border border-gray-100 dark:border-gray-700 p-3 space-y-3">
        <!-- 开关 -->
        <div class="space-y-2">
          <label class="flex items-center gap-2 text-xs text-gray-600 dark:text-gray-300 cursor-pointer whitespace-nowrap">
            <input type="checkbox" :checked="ui.thinkToolsOpen" @change="ui.toggleThinkToolsOpen()" class="w-3.5 h-3.5 accent-gray-800 dark:accent-gray-300" />
            思考区默认展开
          </label>
          <label class="flex items-center gap-2 text-xs text-gray-600 dark:text-gray-300 cursor-pointer whitespace-nowrap">
            <input type="checkbox" :checked="ui.contentExpanded" @change="ui.toggleContentExpanded()" class="w-3.5 h-3.5 accent-gray-800 dark:accent-gray-300" />
            内容框默认展开
          </label>
          <label class="flex items-center gap-2 text-xs text-gray-600 dark:text-gray-300 cursor-pointer whitespace-nowrap">
            <input type="checkbox" :checked="ui.allowToggle" @change="ui.toggleAllowToggle()" class="w-3.5 h-3.5 accent-gray-800 dark:accent-gray-300" />
            允许手动折叠
          </label>
          <label class="flex items-center gap-2 text-xs text-gray-600 dark:text-gray-300 cursor-pointer whitespace-nowrap">
            <input type="checkbox" :checked="ui.notificationEnabled" @change="ui.toggleNotification()" class="w-3.5 h-3.5 accent-gray-800 dark:accent-gray-300" />
            手机通知
          </label>
          <label class="flex items-center gap-2 text-xs text-gray-600 dark:text-gray-300 cursor-pointer whitespace-nowrap">
            <input type="checkbox" :checked="ui.browserHeaded" @change="ui.toggleBrowserHeaded()" class="w-3.5 h-3.5 accent-gray-800 dark:accent-gray-300" />
            有头浏览器
          </label>
          <label class="flex items-center gap-2 text-xs text-gray-600 dark:text-gray-300 cursor-pointer whitespace-nowrap">
            <input type="checkbox" :checked="ui.visionHighRes" @change="ui.toggleVisionHighRes()" class="w-3.5 h-3.5 accent-gray-800 dark:accent-gray-300" />
            高精度图片识别
          </label>
        </div>

        <!-- 分隔线 -->
        <div class="border-t border-gray-100 dark:border-gray-700"></div>

        <!-- 安装更新按钮：仅 Android 端显示 -->
        <button v-if="isAndroid" @click="triggerInstall"
          class="w-full text-xs py-1.5 -mt-1 rounded bg-gray-900 hover:bg-black dark:bg-gray-600 dark:hover:bg-gray-500 text-white transition">
          安装更新
        </button>

        <!-- 字号调节 -->
        <div class="space-y-1.5">
          <div class="flex items-center justify-between text-xs text-gray-500 dark:text-gray-400">
            <span>Aa 字号</span>
            <span class="font-mono tabular-nums text-gray-700 dark:text-gray-200">{{ ui.fontSize }}px</span>
          </div>
          <div class="flex items-center gap-2">
            <button @click="ui.setFontSize(ui.fontSize - 1)"
              class="text-xs w-5 h-5 flex items-center justify-center rounded bg-gray-100 dark:bg-gray-700 text-gray-500 dark:text-gray-400 hover:bg-gray-200 dark:hover:bg-gray-600 transition flex-shrink-0">−</button>
            <input type="range" min="12" max="20" :value="ui.fontSize"
              @input="ui.setFontSize(Number(($event.target as HTMLInputElement).value))"
              class="flex-1 h-1 accent-gray-600 dark:accent-gray-300 cursor-pointer" />
            <button @click="ui.setFontSize(ui.fontSize + 1)"
              class="text-xs w-5 h-5 flex items-center justify-center rounded bg-gray-100 dark:bg-gray-700 text-gray-500 dark:text-gray-400 hover:bg-gray-200 dark:hover:bg-gray-600 transition flex-shrink-0">＋</button>
          </div>
        </div>
      </div>

      <button @click="showSettings = !showSettings"
        class="flex items-center gap-1.5 text-xs text-gray-500 dark:text-gray-400 hover:text-gray-700 dark:hover:text-gray-200 transition w-full">
        <span>⚙</span>
        <span>设置</span>
        <span class="ml-auto text-[10px]">{{ showSettings ? '▼' : '▲' }}</span>
      </button>
    </div>
  </aside>

  <!-- 手机端遮罩 -->
  <div v-if="isMobile && mobileOpen" class="fixed inset-0 bg-black/20 dark:bg-black/50 z-[49]" @click="closeMobile"></div>
  <!-- 手机端左上角：面包屑 + 新会话（垂直排列） -->
  <div v-if="isMobile && !mobileOpen" class="fixed top-2.5 left-2.5 z-40 flex flex-col items-center gap-1.5">
    <!-- 面包屑按钮：点击打开侧边栏 -->
    <button @click="mobileOpen = true"
      class="w-[34px] h-[34px] flex items-center justify-center
             bg-white/90 dark:bg-gray-800/90 backdrop-blur-sm rounded-full
             shadow-[0_1px_4px_rgba(0,0,0,0.12)] dark:shadow-[0_1px_4px_rgba(0,0,0,0.3)]
             border-none cursor-pointer"
      title="会话列表">
      <svg class="w-5 h-5 text-gray-700 dark:text-gray-200" fill="none" stroke="currentColor" viewBox="0 0 24 24">
        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 6h16M4 12h16M4 18h16"/>
      </svg>
    </button>
    <!-- 新会话按钮 -->
    <button @click="handleNewSession"
      class="w-[34px] h-[34px] flex items-center justify-center
             bg-white/90 dark:bg-gray-800/90 backdrop-blur-sm rounded-full
             shadow-[0_1px_4px_rgba(0,0,0,0.12)] dark:shadow-[0_1px_4px_rgba(0,0,0,0.3)]
             border-none cursor-pointer text-gray-700 dark:text-gray-200 text-lg font-medium"
      title="新会话">
      ＋
    </button>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted, watch } from 'vue'
import SessionList from './SessionList.vue'
import AutoScrollToggle from './AutoScrollToggle.vue'
import { useUIStore } from '@/stores/ui'
import { useSessionStore } from '@/stores/sessions'
import { useChatStore } from '@/stores/chat'
import { useWebSocketStore } from '@/stores/websocket'
import { usePlatform } from '@/composables/usePlatform'

const ui = useUIStore()
const sessions = useSessionStore()
const chat = useChatStore()
const wsStore = useWebSocketStore()
const { isAndroid } = usePlatform()

const mobileOpen = ref(false)
const isMobile = ref(window.innerWidth <= 768)
const showSettings = ref(false)
const devices = [
  { key: 'home', label: 'Home', icon: '🏠' },
  { key: 'work', label: 'Work', icon: '💻' },
] as const
const currentDevice = computed(() => {
  const m = location.pathname.match(/^\/([^/]+)/)
  return m ? m[1] : 'home'
})
function switchDevice(device: string) {
  if (device === currentDevice.value) return
  const nb = (window as any).NativeBridge
  if (nb?.switchDevice) {
    nb.switchDevice(device)
  }
}

/** 手动触发已下载 APK 的安装（设置页「安装更新」按钮） */
function triggerInstall() {
  const nb = (window as any).NativeBridge
  if (nb?.triggerInstall) {
    nb.triggerInstall()
  }
}

// 手机端每次打开侧边栏时，设置面板默认收起 + 刷新会话列表
watch(mobileOpen, (open) => {
  if (open) {
    showSettings.value = false
    sessions.loadList()
  }
})

// 桌面端侧边栏展开时刷新会话列表
watch(() => ui.sidebarCollapsed, (collapsed) => {
  if (!collapsed) sessions.loadList()
})

function onResize() {
  isMobile.value = window.innerWidth <= 768
  if (!isMobile.value) mobileOpen.value = false
}

onMounted(() => {
  window.addEventListener('resize', onResize)
})
onUnmounted(() => { window.removeEventListener('resize', onResize) })

const sidebarClass = computed(() => {
  if (isMobile.value) {
    // 手机端：默认 w-0，打开时 mobile-open 样式由 CSS 控制
    return mobileOpen.value ? 'mobile-open' : ''
  }
  // 桌面端：固定宽度或折叠
  return ui.sidebarCollapsed ? 'w-11' : 'w-[240px]'
})


function handleNewSession() {
  // 不发送 abort——旧版只断开 WebSocket 而不通知服务端终止。
  // 服务端检测到连接断开后 task 继续在后台运行完成，结果正常入库。
  // 发送 abort 反而会让服务端取消 task，导致 partial 结果丢失。
  chat.newSession()
  wsStore.disconnect()
  wsStore.connect()
  ui.requestFocusInput()
  // subscribe → history handler 会调 loadList，此处不重复
  closeMobile()
}

function handleSelect(id: string) {
  if (chat.isStreaming) chat.finishStreaming()
  chat.switchSession(id)
  wsStore.disconnect()
  wsStore.connect()
  // subscribe → history handler 会调 loadList，此处不重复
  closeMobile()
}

function handleDelete(id: string) {
  ui.showDialog({
    message: '确定删除该会话吗？',
    confirmText: '删除',
    cancelText: '取消',
    showInput: false,
    callback: (result) => { if (result) sessions.deleteSession(id) }
  })
}

function closeMobile() { mobileOpen.value = false }
</script>
