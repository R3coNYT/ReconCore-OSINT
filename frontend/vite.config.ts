import { fileURLToPath, URL } from 'node:url'

import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: { '@': fileURLToPath(new URL('./src', import.meta.url)) },
  },
  server: {
    port: 5173,
    // En developpement, l'API tourne sur 8000 : evite toute configuration CORS.
    proxy: {
      '/api': { target: 'http://localhost:8000', changeOrigin: true },
      '/health': { target: 'http://localhost:8000', changeOrigin: true },
    },
  },
  build: {
    outDir: 'dist',
    sourcemap: false,
    rollupOptions: {
      output: {
        // Cytoscape is heavy and only used on the graph views: it is split out
        // so it does not weigh down the initial load.
        manualChunks: {
          cytoscape: ['cytoscape'],
          react: ['react', 'react-dom', 'react-router-dom'],
        },
      },
    },
  },
})
