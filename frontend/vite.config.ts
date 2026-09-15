/// <reference types="vitest/config" />
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import path from 'path'

// Backend origin for the dev proxy. Overridable because 8000 is a popular
// default and may already be taken by another project on the same machine.
const apiTarget = process.env.VITE_API_PROXY_TARGET || 'http://localhost:8000';

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      '@': path.resolve(__dirname, './src'),
    },
  },
  test: {
    environment: 'jsdom',
    setupFiles: ['./src/test/setup.ts'],
    globals: true,
    exclude: ['**/node_modules/**', '**/dist/**', '**/e2e/**'],
  },
  server: {
    port: 5173,
    proxy: {
      '/api': {
        target: apiTarget,
        changeOrigin: true,
        // Ensure SSE streaming is not buffered by the proxy
        configure: (proxy) => {
          proxy.on('proxyRes', (proxyRes) => {
            // Disable buffering for event-stream responses
            if (proxyRes.headers['content-type']?.includes('text/event-stream')) {
              proxyRes.headers['cache-control'] = 'no-cache';
              proxyRes.headers['x-accel-buffering'] = 'no';
            }
          });
        },
      },
      '/health': {
        target: apiTarget,
        changeOrigin: true,
      },
    },
  },
  preview: {
    port: 4173,
    proxy: {
      '/api': {
        target: apiTarget,
        changeOrigin: true,
      },
      '/health': {
        target: apiTarget,
        changeOrigin: true,
      },
    },
  },
})

