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
    // One self-hosted VM runs this suite next to the API suite (Postgres plus
    // ~2000 tests) and the Flutter analyzer, so a component test that waits for
    // a committed render can spend seconds queued for a core. The default 5s
    // expired there and failed the gate with no assertion behind it; the poll
    // budget the component tests use is `POLL_BUDGET_MS` in
    // `src/test-support/poll-budget.ts`.
    testTimeout: 30_000,
    hookTimeout: 30_000,
  },
});
