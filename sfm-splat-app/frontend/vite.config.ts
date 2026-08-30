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
    // Listen on every interface, so the staging box on the LAN is reachable by
    // its address instead of only from its own console. `start.bat` passes
    // --host as well; this is the default for anyone who runs `npm run dev`
    // by hand. Nothing here is an invitation to the internet - CLAUDE.md §1's
    // "no VPS / remote deployment" still stands, this is one trusted subnet.
    host: true,
    // Vite 5.4 rejects a request whose Host header is a name it does not know
    // (an IP is always allowed). The staging box is likely to be reached by its
    // Windows hostname, and the failure is an opaque "Blocked request".
    allowedHosts: true,
    proxy: {
      // The proxy runs on the server, so its targets stay on the loopback: the
      // browser talks to this origin only, which is what lets `api/client.ts`
      // and `useWebSocket.ts` be same-origin and host-agnostic.
      '/api': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
      },
      '/static': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
      },
      '/ws': {
        target: 'ws://127.0.0.1:8000',
        ws: true,
      },
    },
  },
})
