import react from "@vitejs/plugin-react-swc";
import { defineConfig, loadEnv } from "vite";

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), "");
  // In docker dev, set VITE_PROXY_TARGET=http://backend:8000
  // In docker-less dev, set VITE_PROXY_TARGET=http://localhost:8000
  const proxyTarget = (
    process.env.VITE_PROXY_TARGET ||
    env.VITE_PROXY_TARGET ||
    "http://backend:8000"
  )?.trim();

  return {
    plugins: [react()],
    server: {
      host: "0.0.0.0",
      port: 5173,
      strictPort: true,
      hmr: {
        protocol: "ws",
        clientPort: 8080,
      },
      ...(proxyTarget
        ? {
            proxy: {
              "/api": {
                target: proxyTarget,
                changeOrigin: true,
              },
            },
          }
        : {}),
    },
    preview: {
      port: 5173,
      strictPort: true,
    },
    test: {
      environment: "jsdom",
      globals: true,
      setupFiles: ["./src/setupTests.ts"],
      include: ["src/**/*.{test,spec}.tsx"],
    },
  };
});
