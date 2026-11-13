import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

/**
 * Deliberately a different port from the main SAGE frontend (5173): both apps
 * can run against the same backend at once, which is what makes comparing the
 * classic UI with this workbench possible.
 */
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5174,
    proxy: {
      "/api": { target: "http://127.0.0.1:8000", changeOrigin: true },
      "/health": { target: "http://127.0.0.1:8000", changeOrigin: true },
    },
  },
});
