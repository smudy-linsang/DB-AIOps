/// <reference types="vitest" />
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  server: {
    port: 3000,
    proxy: {
      // API 请求代理到 Django 后端
      '/api': {
        target: 'http://localhost:8000',
        changeOrigin: true,
      },
      // 静态文件和媒体文件
      '/static': {
        target: 'http://localhost:8000',
        changeOrigin: true,
      },
      '/media': {
        target: 'http://localhost:8000',
        changeOrigin: true,
      }
    }
  },
  build: {
    outDir: 'dist',
    sourcemap: false,
    // 构建优化
    rollupOptions: {
      output: {
        // Vite 8 / Rolldown 只接受函数形式。按模块路径分包，避免对象形式
        // 在升级后构建期抛 "manualChunks is not a function"。
        manualChunks(id) {
          if (!id.includes('node_modules')) return undefined
          if (id.includes('/echarts/') || id.includes('/zrender/')) return 'vendor-echarts'
          if (id.includes('/antd/') || id.includes('/@ant-design/')) return 'vendor-antd'
          if (id.includes('/recharts/')) return 'vendor-charts'
          if (id.includes('/react/') || id.includes('/react-dom/') ||
              id.includes('/react-router')) return 'vendor-react'
          return undefined
        }
      }
    }
  },
  test: {
    globals: true,
    environment: 'jsdom',
    setupFiles: './src/test/setup.js',
    css: true,
  }
})
