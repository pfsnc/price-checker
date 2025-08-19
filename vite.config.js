import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  base: '/price-checker/',
  build: {
    rollupOptions: {
      external: ['/config.js'], // 將 config.js 標記為外部資源
    }
  },
  publicDir: 'public',
})
