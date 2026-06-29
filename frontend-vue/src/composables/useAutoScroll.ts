// ========================= useAutoScroll =========================
// 管理聊天容器的自动滚动行为：滚轮向上→关闭，接近底部→开启
import { nextTick, type Ref } from 'vue';
import { useUIStore } from '@/stores/ui';

export function useAutoScroll(containerRef: Ref<HTMLElement | null>) {
  const ui = useUIStore();

  function scrollToBottom() {
    if (!ui.autoScroll) return;
    const el = containerRef.value;
    if (!el) return;
    ui.programScrolling = true;
    el.scrollTop = el.scrollHeight;
    requestAnimationFrame(() => { ui.programScrolling = false; });
  }

  async function scrollToBottomImmediate() {
    const el = containerRef.value;
    if (!el) return;
    ui.programScrolling = true;
    el.scrollTop = el.scrollHeight;
    await nextTick();
    requestAnimationFrame(() => { ui.programScrolling = false; });
  }

  function onWheel(e: WheelEvent) {
    if (e.deltaY < 0 && ui.autoScroll) {
      ui.setAutoScroll(false);
    }

    // 内部可滚动元素（代码块中的 pre）冒泡到容器时，改为滚动容器
    const target = e.target as HTMLElement | null;
    const inner = target?.closest('.think-tools-content pre, .user-msg-content, .tool-args, .tool-result');
    if (inner) {
      e.preventDefault();
      const el = containerRef.value;
      if (el) el.scrollTop += e.deltaY;
    }
  }

  function onScroll() {
    if (ui.programScrolling) return;
    const el = containerRef.value;
    if (!el) return;
    const distanceToBottom = el.scrollHeight - el.scrollTop - el.clientHeight;
    if (distanceToBottom < 5) {
      if (!ui.autoScroll) ui.setAutoScroll(true);
    } else {
      if (ui.autoScroll) ui.setAutoScroll(false);
    }
  }

  function jumpToBottom() {
    ui.setAutoScroll(true);
    scrollToBottomImmediate();
  }

  return { scrollToBottom, scrollToBottomImmediate, onWheel, onScroll, jumpToBottom };
}
