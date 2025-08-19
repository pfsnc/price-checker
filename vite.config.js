import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  base: '/price-checker/',
  build: {
    outDir: 'dist',
    rollupOptions: {
      external: ['/price-checker/config.js']
    }
  },
  publicDir: 'public'
})
