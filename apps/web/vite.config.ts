import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: { port: 5173 },
  // Vitest and Playwright both name their files ``*.spec.ts`` / ``*.test.ts``. Without an
  // explicit include, ``vitest run`` picks up ``e2e/**`` and every browser spec fails with
  // "Playwright Test did not expect test.describe() to be called here" -- six red files
  // that say nothing about the app. Unit and component tests live under ``src``; ``e2e``
  // belongs to Playwright (see playwright.config.ts).
  test: {
    include: ["src/**/*.{test,spec}.{ts,tsx}"],
    exclude: ["node_modules/**", "dist/**", "e2e/**", "test-results/**"],
  },
});
