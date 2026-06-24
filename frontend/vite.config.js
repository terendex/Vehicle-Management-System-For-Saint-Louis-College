import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// In Docker the backend is reachable via the service name, not 127.0.0.1.
// Set BACKEND_URL=http://backend:8000 in docker-compose; falls back to
// localhost for running Vite outside Docker.
const backendUrl = process.env.BACKEND_URL || 'http://127.0.0.1:8000'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    host: '0.0.0.0',
    proxy: {
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
    },
  },
})
