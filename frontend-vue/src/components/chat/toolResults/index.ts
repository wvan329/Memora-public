// ========================= 工具结果渲染器注册表 =========================
// 每种 resultType 对应一个组件，消除 ToolCard 中的 if-else 链。
// 新增工具类型只需在此注册 + 创建组件，无需改动 ToolCard。
import { defineAsyncComponent } from 'vue';
import type { Component } from 'vue';

export const resultRenderers: Record<string, Component> = {
  download: defineAsyncComponent(() => import('./DownloadResult.vue')),
  image: defineAsyncComponent(() => import('./ImageResult.vue')),
  files: defineAsyncComponent(() => import('./FilesResult.vue')),
  delegate: defineAsyncComponent(() => import('./DelegateResult.vue')),
  vision_result: defineAsyncComponent(() => import('./VisionResult.vue')),
  json: defineAsyncComponent(() => import('./JsonResult.vue')),
};
