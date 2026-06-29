// ========================= 滚动容器自动滚动 =========================
// 管理任意滚动容器的自动滚动行为。
// 与 useAutoScroll 的区别：useAutoScroll 管理"全局"autoScroll 开关（存 ui store），
// useScrollContainer 管理"单个容器内多页面"的独立自动滚动，不依赖全局 ui store。
// 适用于 AppDialog 等有多个滚动页面的独立容器。

import { reactive, nextTick } from 'vue';

export function useScrollContainer() {
  const autoScrollFlags = reactive<Record<number, boolean>>({});
  let _programScrolling = false;

  /** 如果该页自动滚动开启，则滚到底部 */
  function scrollPageToBottom(pageIdx: number, getEl: (idx: number) => HTMLElement | null) {
    if (!autoScrollFlags[pageIdx]) return;
    const el = getEl(pageIdx);
    if (!el) return;
    _programScrolling = true;
    el.scrollTop = el.scrollHeight;
    requestAnimationFrame(() => { _programScrolling = false; });
  }

  /** 页面滚动事件：判断用户是否在底部，开关自动滚动 */
  function onPageScroll(e: Event, pageIdx: number) {
    if (_programScrolling) return;
    const el = e.target as HTMLElement;
    const dist = el.scrollHeight - el.scrollTop - el.clientHeight;
    autoScrollFlags[pageIdx] = dist < 5;
  }

  /** 页面滚轮事件：向上滚即关闭自动滚动 */
  function onPageWheel(e: WheelEvent, pageIdx: number) {
    if (e.deltaY < 0 && autoScrollFlags[pageIdx]) {
      autoScrollFlags[pageIdx] = false;
    }
  }

  /** 显式设置某页的自动滚动开关 */
  function setAutoScroll(pageIdx: number, val: boolean) {
    autoScrollFlags[pageIdx] = val;
  }

  return { autoScrollFlags, scrollPageToBottom, onPageScroll, onPageWheel, setAutoScroll };
}
