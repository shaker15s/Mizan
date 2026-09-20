import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

/**
 * Vite dev server config for the MIZAN Phase 10 React/TS migration.
 *
 * In development we proxy /api requests to the Python POC web server
 * (started separately on http://127.0.0.1:8765 by `python -m poc.web_server`
 * or `make cockpit`). Same-origin requests avoid CORS issues and keep the
 * strict production CSP (`default-src 'self'`) working against the
 * Phase-9-hardened API.
 */
export default defineConfig({
  plugins: [react()],
  server: {
    host: "0.0.0.0",
    port: 5173,
    strictPort: false,
    proxy: {
      "/api": {
        target: "http://127.0.0.1:8765",
        changeOrigin: true,
      },
    },
  },
  build: {
    outDir: "dist",
    sourcemap: true,
  },
});
