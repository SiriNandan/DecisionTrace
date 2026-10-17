import { defineConfig, loadEnv } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), "");
  return {
    plugins: [react()],
    define: {
      "process.env.VITE_API_URL": JSON.stringify(
        env.VITE_API_URL || process.env.VITE_API_URL || "http://localhost:8000"
      ),
      "process.env.VITE_API_KEY": JSON.stringify(
        env.VITE_API_KEY || process.env.VITE_API_KEY || ""
      ),
      "process.env.VITE_USE_MOCKS": JSON.stringify(
        env.VITE_USE_MOCKS || process.env.VITE_USE_MOCKS || "false"
      ),
    },
    server: {
      port: 5173,
      host: true,
    },
  };
});

