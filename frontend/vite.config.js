import { defineConfig } from 'vite'

// Dev: Vite on :5173 proxies the API to Flask on :8080 (run with HAKAM_LOCAL=1).
// Build: emits dist/, which Flask serves as static files in production.
export default defineConfig({
  // Served from a custom apex domain (thehakam.com) at the root.
  base: '/',
  server: {
    port: 5173,
    proxy: {
      '/api': 'http://localhost:8080',
    },
  },
  build: {
    outDir: 'dist',
    emptyOutDir: true,
  },
})
