import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import tailwindcss from '@tailwindcss/vite'
import { fileURLToPath, URL } from 'node:url'

export default defineConfig(({ mode }) => ({
  // 生产构建时静态资源路径加 /ai/ 前缀，匹配 nginx 反向代理规则
  base: mode === 'production' ? './' : '/',
  plugins: [
    vue(),
    tailwindcss(),
  ],
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
    },
  },
  server: {
    proxy: {
      '/ws': { target: 'http://127.0.0.1:8007', ws: true },
      '/api': { target: 'http://127.0.0.1:8007' },
      '/sessions': { target: 'http://127.0.0.1:8007' },
      '/turns': { target: 'http://127.0.0.1:8007' },
    },
  },
}))