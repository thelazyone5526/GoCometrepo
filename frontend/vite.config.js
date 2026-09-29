import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    // Forward every /api request to the FastAPI backend. The browser only ever talks to
    // the Vite origin, so the backend needs no CORS setup. 127.0.0.1 (not "localhost")
    // matches the address uvicorn binds to and avoids an IPv6 lookup mismatch.
    proxy: {
      '/api': 'http://127.0.0.1:8000',
    },
  },
})
