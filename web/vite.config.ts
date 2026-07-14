import { defineConfig, loadEnv } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, ".", "");
  const apiTarget = env.VITE_API_TARGET || "http://127.0.0.1:8000";

  return {
    plugins: [react()],
    server: {
      port: 5173,
      proxy: {
        "/api": apiTarget,
        "/media": apiTarget,
      },
    },
    preview: {
      port: 4173,
      proxy: {
        "/api": apiTarget,
        "/media": apiTarget,
      },
    },
  };
});
