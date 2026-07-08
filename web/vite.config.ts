import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// 开发时把 /api 和 /media 代理到 FastAPI 后端（默认 8000 端口）
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": "http://localhost:8000",
      "/media": "http://localhost:8000",
    },
  },
});
