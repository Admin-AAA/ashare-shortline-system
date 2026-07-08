import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

const apiProxyTarget = process.env.ASHARE_API_PROXY ?? "http://localhost:8765";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5174,
    allowedHosts: true,
    proxy: {
      "/api": apiProxyTarget,
    },
  },
  build: {
    // three.js 是 WaveBackground 的强依赖（~600KB），从入口 chunk 拆出独立 vendor chunk：
    // 入口 chunk 从 ~724KB 降到 ~120KB，three 独立缓存 + 并行加载，首屏解析更快。
    chunkSizeWarningLimit: 650,
    rollupOptions: {
      output: {
        // Vite 8 (rolldown) 要求 manualChunks 为函数形式
        manualChunks(id: string) {
          if (id.includes("node_modules")) {
            if (id.includes("/three/")) return "vendor-three";
            if (
              id.includes("/react/") ||
              id.includes("/react-dom/") ||
              id.includes("/scheduler/")
            ) {
              return "vendor-react";
            }
          }
          return undefined;
        },
      },
    },
  },
});
