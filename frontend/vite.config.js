import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  server: {
    port: 3000,
    proxy: {
      // API 请求代理到 Django 后端
      '/api': {
        target: 'http://host.docker.internal:8000',
        changeOrigin: true,
      },
      // 静态文件和媒体文件
      '/static': {
        target: 'http://host.docker.internal:8000',
        changeOrigin: true,
      },
      '/media': {
        target: 'http://host.docker.internal:8000',
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
        manualChunks: {
          'vendor-react': ['react', 'react-dom', 'react-router-dom'],
          'vendor-antd': ['antd'],
          'vendor-charts': ['recharts']
        }
      }
    }
  }
})
