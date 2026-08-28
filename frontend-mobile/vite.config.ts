import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    host: process.env.ULTRON_DEV_HOST || "127.0.0.1",
    port: 5174,
    allowedHosts: (process.env.ULTRON_DEV_ALLOWED_HOSTS || "localhost,127.0.0.1").split(",").map((x) => x.trim()).filter(Boolean),
    proxy: {
      "/api": { target: "http://127.0.0.1:8000", changeOrigin: true },
      "/ws": { target: "ws://127.0.0.1:8000", ws: true },
    },
  },
  preview: {
    host: process.env.ULTRON_DEV_HOST || "127.0.0.1",
    port: 5174,
    allowedHosts: (process.env.ULTRON_DEV_ALLOWED_HOSTS || "localhost,127.0.0.1").split(",").map((x) => x.trim()).filter(Boolean),
    proxy: {
      "/api": { target: "http://127.0.0.1:8000", changeOrigin: true },
      "/ws": { target: "ws://127.0.0.1:8000", ws: true },
    },
  },
});
