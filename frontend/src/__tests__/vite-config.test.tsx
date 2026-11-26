// @vitest-environment node
import { afterEach, expect, it, vi } from "vitest";
import { loadConfigFromFile } from "vite";

afterEach(() => vi.unstubAllEnvs());

it("uses the app-facing development port and configured hostname", async () => {
  vi.stubEnv("DEV_HTTP_PORT", "18123");
  vi.stubEnv("APP_HOST", "dev.example");
  const loaded = await loadConfigFromFile(
    { command: "serve", mode: "development" },
    "vite.config.ts",
  );
  expect(loaded?.config.server?.hmr).toMatchObject({ clientPort: 18123 });
  expect(loaded?.config.server?.allowedHosts).toEqual(["dev.example"]);
});
