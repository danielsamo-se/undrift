import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

const api = process.env.UNDRIFT_API ?? "http://127.0.0.1:8100";

export default defineConfig({
  plugins: [react()],
  server: { host: "127.0.0.1", port: 5173, strictPort: true, proxy: { "/api": api } },
  preview: { host: "127.0.0.1", port: 4173 },
  build: { outDir: "dist", sourcemap: false, chunkSizeWarningLimit: 800 },
});
