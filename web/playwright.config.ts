import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "./tests",
  use: { browserName: "chromium", viewport: { width: 1200, height: 900 } },
});
