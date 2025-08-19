import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  base: '/price-checker/',
  build: {
    rollupOptions: {
      external: ['/config.js']
    }
  },
  publicDir: 'public'
})
