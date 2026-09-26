import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'
import { defineConfig } from 'vite'

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    // The browser only ever talks to Vite; /api/* is forwarded to FastAPI,
    // which serves the API under /api (as in production). Same origin, no CORS.
    proxy: {
      '/api': { target: 'http://localhost:8000' },
    },
  },
})
