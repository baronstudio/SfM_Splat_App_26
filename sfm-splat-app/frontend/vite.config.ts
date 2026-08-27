import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'
import path from "path"

// https://vitejs.dev/config/
export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
    },
    // Single React instance. Without this, a second copy reached through a
    // transitive path gets its own dispatcher and every hook call throws
    // "dispatcher is null" as soon as the two are mixed in one render.
    dedupe: ['react', 'react-dom'],
  },
  optimizeDeps: {
    // Pre-bundle every Radix entry point at server start. Several of these are
    // only reachable through lazily-loaded wizard steps, so Vite used to
    // discover them mid-session, re-optimize, and bump the browserHash — which
    // leaves an already-open tab holding modules from both passes.
    include: [
      'react',
      'react-dom',
      'radix-ui',
      '@radix-ui/react-dialog',
      '@radix-ui/react-label',
      '@radix-ui/react-radio-group',
      '@radix-ui/react-select',
      '@radix-ui/react-slider',
      '@radix-ui/react-switch',
      '@radix-ui/react-slot',
      // Same reason: the 3D viewer is only reachable through wizard steps 3-5.
      'three',
      'three/examples/jsm/controls/OrbitControls.js',
      '@mkkellogg/gaussian-splats-3d',
    ],
  },
  server: {
    proxy: {
      '/api': {
        target: 'http://localhost:8000',
        changeOrigin: true,
      },
      '/static': {
        target: 'http://localhost:8000',
        changeOrigin: true,
      },
      '/ws': {
        target: 'ws://localhost:8000',
        ws: true,
      },
    },
  },
})
