import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

export default defineConfig({
  // Electron loads the production UI via file://, so assets must resolve relatively.
  base: "./",
  plugins: [react(), tailwindcss()],
  server: {
    host: process.env.ULTRON_DEV_HOST || "127.0.0.1",
    port: 5173,
    allowedHosts: (process.env.ULTRON_DEV_ALLOWED_HOSTS || "localhost,127.0.0.1").split(",").map((x) => x.trim()).filter(Boolean),
    proxy: {
      "/api": { target: "http://127.0.0.1:8000", changeOrigin: true },
      "/ws": { target: "ws://127.0.0.1:8000", ws: true },
    },
  },
  preview: {
    host: process.env.ULTRON_DEV_HOST || "127.0.0.1",
    port: 5173,
    allowedHosts: (process.env.ULTRON_DEV_ALLOWED_HOSTS || "localhost,127.0.0.1").split(",").map((x) => x.trim()).filter(Boolean),
    proxy: {
      "/api": { target: "http://127.0.0.1:8000", changeOrigin: true },
      "/ws": { target: "ws://127.0.0.1:8000", ws: true },
    },
  },
});
