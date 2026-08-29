import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// In Docker the backend is reachable via the service name, not 127.0.0.1.
// Set BACKEND_URL=http://backend:8000 in docker-compose; falls back to
// localhost for running Vite outside Docker.
const backendUrl = process.env.BACKEND_URL || 'http://127.0.0.1:8000'

// Shared by dev (`npm run dev`) and preview (`npm run preview`) so the built
// bundle can be served with the same /api + /ws proxying. Preview is what you
// want behind a tunnel — dev mode ships hundreds of unbundled module requests,
// which is slow when every request round-trips through the tunnel.
const proxy = {
  // MJPEG streaming endpoint — no timeout, no buffering
  '/api/vehicles/parking-zones': {
    target: backendUrl,
    changeOrigin: true,
    proxyTimeout: 0,
    timeout: 0,
    configure: (proxy) => {
      // Remove Accept-Encoding so the server doesn't gzip the MJPEG stream
      proxy.on('proxyReq', (proxyReq) => {
        proxyReq.removeHeader('accept-encoding')
      })
    },
  },
  // All other API calls
  '/api': {
    target: backendUrl,
    changeOrigin: true,
  },
  // WebSocket proxy — avoids ERR_NETWORK_ACCESS_DENIED on Windows
  '/ws': {
    target: backendUrl,
    changeOrigin: true,
    ws: true,
  },
}

const allowedHosts = ['preconcurrently-inorganic-nicolle.ngrok-free.dev', '.trycloudflare.com']

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],

  // Pre-bundle every heavy dependency up front. Without this Vite discovers
  // them lazily mid-session, then re-optimises and force-reloads the page —
  // the single worst stall when developing through a tunnel.
  optimizeDeps: {
    include: [
      'react', 'react-dom', 'react-router-dom',
      'axios', 'zustand',
      'lucide-react', 'date-fns',
      'recharts',
      'jspdf',
      'qrcode.react', 'jsqr', 'react-webcam',
      '@tanstack/react-query', '@tanstack/react-table',
      'react-hook-form', '@hookform/resolvers', 'zod',
    ],
  },

  server: {
    host: '0.0.0.0',
    allowedHosts,
    proxy,
    // Transform these at startup so the first request doesn't pay for it.
    warmup: {
      clientFiles: [
        './src/main.jsx',
        './src/App.jsx',
        './src/pages/Login/LoginPage.jsx',
        './src/stores/authStore.js',
        './src/api/axios.js',
        './src/components/Layout/AdminLayout.jsx',
      ],
    },
  },
  preview: {
    host: '0.0.0.0',
    port: 4173,
    allowedHosts,
    proxy,
  },
})
