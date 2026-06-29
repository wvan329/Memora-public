// ========================= 平台检测 =========================
// 统一所有平台判断逻辑，消除散落在 store 和组件中的 isMobileDevice / isAndroid / devicePath。

import { computed } from 'vue';
import { getPathPrefix } from '@/utils/platform';

export function usePlatform() {
  const isMobile = computed(() => /Android|iPhone|iPad|iPod/i.test(navigator.userAgent));
  const isAndroid = computed(() => typeof (window as any).NativeBridge !== 'undefined');

  const devicePath = computed(() => getPathPrefix());

  return { isMobile, isAndroid, devicePath };
}
