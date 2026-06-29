<template>
  <div class="tool-item pl-1.5">
    <div class="text-sm font-semibold text-ai-tool dark:text-blue-400">
      🔧 {{ item.name }}
      <span v-if="resolvedDialogType === 'delegate'"
        class="delegate-link text-xs text-red-600 dark:text-red-400 cursor-pointer underline hover:text-red-800 dark:hover:text-red-300 ml-1"
        @click.stop="openDelegate">查看详情 →</span>
      <span v-if="resolvedDialogType === 'ask_user'"
        class="text-xs ml-1 underline cursor-pointer"
        :class="item.result ? 'text-gray-400 dark:text-gray-500 hover:text-gray-600 dark:hover:text-gray-400' : 'text-blue-600 dark:text-blue-400 hover:text-blue-800 dark:hover:text-blue-300'"
        @click.stop="openAskDialog()">打开弹窗 →</span>
      <span v-if="resolvedDialogType === 'confirm'"
        class="text-xs ml-1 underline cursor-pointer"
        :class="item.result ? 'text-gray-400 dark:text-gray-500 hover:text-gray-600 dark:hover:text-gray-400' : 'text-blue-600 dark:text-blue-400 hover:text-blue-800 dark:hover:text-blue-300'"
        @click.stop="openRestartConfirm()">打开确认 →</span>
      <span v-if="resolvedDialogType === 'vision'"
        class="text-xs ml-1 underline cursor-pointer"
        :class="item.result ? 'text-gray-400 dark:text-gray-500 hover:text-gray-600 dark:hover:text-gray-400' : 'text-blue-600 dark:text-blue-400 hover:text-blue-800 dark:hover:text-blue-300'"
        @click.stop="openVisionDialog()">打开弹窗 →</span>
    </div>

    <pre v-if="item.args" @mouseup="toggleTruncate($event, 'args')"
      class="tool-args truncatable text-sm mt-0 mb-0 px-2 py-0 pr-0 overflow-x-auto whitespace-pre-wrap break-words scrollbar-none
             text-ai-tool dark:text-blue-400 border-l-[3px] border-transparent"
      :class="{ 'truncated clamp-4': argsTruncated }">{{ item.args }}</pre>

    <div v-if="isPending" class="flex items-center gap-2 mt-1 text-sm text-gray-400 dark:text-gray-500">
      <span class="inline-block w-3 h-3 border-2 border-gray-300 dark:border-gray-600 border-t-blue-400 rounded-full animate-spin"></span>
      工具执行中…
    </div>

    <div v-if="hasResult" class="tool-result-container mt-0 pt-0">
      <span class="text-sm font-semibold text-gray-500 dark:text-gray-400 mb-0">结果</span>
      <div @mouseup="toggleTruncate($event, 'result')"
        class="truncatable text-sm mt-0 [&_p:empty]:hidden"
        :class="{ 'truncated clamp-4': resultTruncated, 'px-2 py-0 pr-0 overflow-x-auto whitespace-pre-wrap scrollbar-none text-ai-tool dark:text-blue-400 border-l-[3px] border-transparent': item.resultType === 'json' || item.resultType === 'delegate' }">
        <component :is="resultComponent" :item="item" />
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, watch } from 'vue'
import { renderMarkdown } from '@/utils/markdown'
import { getDialogType } from '@/utils/toolResult'
import { resultRenderers } from './toolResults'
import type { ToolItemState, FileItem, ToolDialogType } from '@/types/chat'
import { useAuthStore } from '@/stores/auth'
import { useUIStore } from '@/stores/ui'
import { useChatStore } from '@/stores/chat'
import { useWebSocketStore } from '@/stores/websocket'
import ImageCard from './ImageCard.vue'
import ImageViewer from './ImageViewer.vue'
import type { ImageItem } from './ImageViewer.vue'

const props = defineProps<{ item: ToolItemState }>()
const auth = useAuthStore()
const ui = useUIStore()

const argsTruncated = ref(!ui.contentExpanded)
const resultTruncated = ref(!ui.contentExpanded)
watch(() => ui.contentExpanded, (v) => { argsTruncated.value = !v; resultTruncated.value = !v })

const hasResult = computed(() => {
  if (!props.item.result && !props.item.images?.length && !props.item.files?.length) return false
  // pending 不算有结果
  if (isPending.value) return false
  return true
})
const isPending = computed(() => {
  if (!props.item.name) return false
  // _done 标记：tool_result chunk 到达后为 true（空结果也算完成）
  if (props.item._done === true) return false
  // _done 未设置（实时流式 tool_call_name 后、tool_result 前）
  // 或 _done 被重置为 false（pending placeholder JSON）
  return true
})

// 弹窗类型：优先使用新字段 dialogType，历史数据回退到 getDialogType（唯一真相源）
const resolvedDialogType = computed(() => props.item.dialogType || getDialogType(props.item.name))

/** 根据 resultType 选择对应的渲染组件（兜底到 json） */
const resultComponent = computed(() => resultRenderers[props.item.resultType] || resultRenderers.json)
function openDelegate() {
  // 优先通过 dialogId 恢复弹窗（包括单任务和多任务）
  const eid = props.item.dialogId
  if (eid) {
    const ex = ui.dialogs.find(e => e.id === eid)
    if (ex) {
      ui.restoreDialog(eid)
      return
    }
  }

  // 有 batchSessions → 构建只读预览弹窗
  if (props.item.batchSessions && props.item.batchSessions.length > 0) {
    const sessions = props.item.batchSessions
    // 从 tool args 中提取 task 描述（batchSessions 可能不含 task 字段，但 args 一定含）
    const taskDescs: string[] = []
    try {
      const args = JSON.parse(props.item.args || '{}')
      const rawTasks = args.tasks as Array<Record<string, unknown>> | undefined
      if (rawTasks) taskDescs.push(...rawTasks.map(t => String(t.task || '')))
    } catch { /* */ }
    let parsedResult: Record<string, unknown> | null = null
    try { parsedResult = JSON.parse(props.item.result) } catch { /* */ }
    const pages = sessions.map((s, i) => ({
      message: `<div class="text-sm text-gray-500 dark:text-gray-400 mb-2">📋 任务 ${i + 1}: ${s.task || taskDescs[i] || ''}</div>\n\n\n${parsedResult?.sessions ? String((parsedResult.sessions as Array<Record<string,unknown>>)[i]?.result || '') : '（结果已折叠）'}`,
      options: [] as string[],
      confirm_text: '确认',
      cancel_text: '取消',
    }))
    ui.showDialog({
      message: pages[0]?.message || '',
      options: [],
      confirmText: '',
      cancelText: '',
      showInput: false,
      pages,
      hideActions: true,
      callback: null,
    })
    return
  }

  // 单任务委托（旧历史）：打开子会话新窗口
  if (props.item.sessionUuid) {
  const chatStore = useChatStore()
  const wsStore = useWebSocketStore()
  chatStore.switchSession(props.item.sessionUuid)
  wsStore.disconnect()
  wsStore.connect()
}
}

function openVisionDialog() {
  // 优先通过 dialogId 恢复流式弹窗
  const eid = props.item.dialogId
  if (eid) {
    const ex = ui.dialogs.find(e => e.id === eid)
    if (ex) { ui.restoreDialog(eid); return }
  }
  // 兜底：构建静态预览（历史加载时 dialogId 不存在）
  let imgHtml = ''
  let text = ''
  let question = ''
  try {
    const p = JSON.parse(props.item.result)
    if (p.images && Array.isArray(p.images)) {
      imgHtml = p.images.map((img: {url:string, size:string}) =>
        `<img src="${img.url}" alt="${img.size}" class="cursor-pointer rounded-lg max-w-full" style="max-height:200px" />`
      ).join('\n') + '\n\n'
    }
    text = (p.text as string) || ''
  } catch { /* */ }
  // 从 tool args 中提取 question
  try {
    const args = JSON.parse(props.item.args || '{}')
    question = args.question || ''
  } catch { /* */ }
  let msg = ''
  if (question) {
    msg += `<div class="text-sm text-gray-500 dark:text-gray-400 mb-2">🔍 ${question}</div>\n\n`
  }
  msg += imgHtml + text
  ui.showDialog({
    message: msg,
    options: [],
    confirmText: '',
    cancelText: '',
    showInput: false,
    hideActions: true,
    callback: null,
  })
}

function openRestartConfirm() {
  const msg='确定要重启 AI Agent 服务吗？重启期间服务会短暂中断（约 2-3 秒）。'
  // 已交互 → 只读预览（无按钮）
  if (props.item.result) { ui.showDialog({message:msg,options:[],confirmText:'',cancelText:'',showInput:false,hideActions:true,callback:null}); return }
  // 未交互 → 优先通过 dialogId 恢复
  const eid=props.item.dialogId
  if (eid) { const ex=ui.dialogs.find(e=>e.id===eid); if(ex){ ui.restoreDialog(eid); return } }
  // 兜底：通过 lastMinimizedId 恢复（与 ask_user 一致）
  const fid=ui.lastMinimizedId
  if (fid) { const ex=ui.dialogs.find(e=>e.id===fid); if(ex){ ui.restoreDialog(fid); return } }
  // 弹窗已关闭 → 只读预览
  ui.showDialog({message:msg,options:[],confirmText:'',cancelText:'',showInput:false,hideActions:true,callback:null})
}

function openAskDialog() {
  let pages:Array<{message:string;options:string[];confirm_text:string;cancel_text:string}>=[]
  try{const a=JSON.parse(props.item.args||'{}');const rp=a.pages as Array<Record<string,unknown>>|undefined;if(rp?.length) pages=rp.map(p=>({message:(p.message as string)||'',options:(p.options as string[])||[],confirm_text:(p.confirm_text as string)||'确认',cancel_text:(p.cancel_text as string)||'取消'}))}catch{}
  const eid=props.item.dialogId
  if(eid){const ex=ui.dialogs.find(e=>e.id===eid);if(ex){if(pages.length>0)ui.updateDialog(eid,{pages,currentPageIndex:0,message:pages[0]?.message||''});ui.dialogResolved=!!props.item.result;ui.restoreDialog(eid);return}
    ui.showDialog({message:pages[0]?.message||'',options:pages[0]?.options||[],confirmText:'',cancelText:'',showInput:false,pages:pages.length>1?pages:[],hideActions:true,callback:null});return}
  const fid=ui.lastMinimizedId;if(fid){const ex=ui.dialogs.find(e=>e.id===fid);if(ex){if(pages.length>0)ui.updateDialog(fid,{pages,currentPageIndex:0,message:pages[0]?.message||''});ui.dialogResolved=!!props.item.result;ui.restoreDialog(fid);return}}
  ui.showDialog({message:pages[0]?.message||'',options:pages[0]?.options||[],confirmText:'',cancelText:'',showInput:false,pages:pages.length>1?pages:[],hideActions:true,callback:null})
}

function toggleTruncate(e:Event,type:'args'|'result'){if(!ui.allowToggle)return;const sel=window.getSelection();if(sel&&sel.toString().trim().length>0)return;const target=e.target as HTMLElement;if(target.tagName==='IMG')return;if(type==='args')argsTruncated.value=!argsTruncated.value;else resultTruncated.value=!resultTruncated.value}
</script>
