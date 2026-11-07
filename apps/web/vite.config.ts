import { defineConfig, loadEnv } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig(({ mode }) => {
  // `process` is not typed here and the app has no @types/node; `loadEnv` reads
  // the same environment without pulling Node types into a browser build.
  const env = loadEnv(mode, ".", "");
  return {
    plugins: [react()],
    server: {
      port: 5173,
      // The client calls relative `/api/v1/...` URLs so the same bundle works
      // behind the ingress and in production, where `/api` is split to the API
      // Service. On the dev server nothing serves that prefix, so proxy it —
      // without this every request 404s against Vite and local development
      // silently needs an `API_BASE` the app does not have.
      proxy: {
        "/api": {
          target: env.VITE_API_PROXY || "http://127.0.0.1:8000",
          changeOrigin: true,
        },
      },
    },
    // Vitest and Playwright both name their files ``*.spec.ts`` / ``*.test.ts``. Without an
    // explicit include, ``vitest run`` picks up ``e2e/**`` and every browser spec fails with
    // "Playwright Test did not expect test.describe() to be called here" -- six red files
    // that say nothing about the app. Unit and component tests live under ``src``; ``e2e``
    // belongs to Playwright (see playwright.config.ts).
    test: {
      include: ["src/**/*.{test,spec}.{ts,tsx}"],
      exclude: ["node_modules/**", "dist/**", "e2e/**", "test-results/**"],
      // The gate runs this suite on one self-hosted VM alongside the API suite (a
      // Postgres fixture plus ~2000 tests). Vitest's default here is one fork per CPU,
      // so five concurrent jobs turned into ~35 jsdom workers on eight cores: every
      // worker got a fraction of a core and the latency-sensitive component tests timed
      // out with nothing failing inside them. Two forks cost wall clock (the suite is
      // CPU-bound either way) and buy back per-test latency, which is what the polling
      // tests actually depend on.
      pool: "forks",
      poolOptions: { forks: { minForks: 1, maxForks: 2 } },
      // A component test that waits for a committed render can still queue behind a
      // resize of the fork's heap; the default 5s expired there and failed the gate
      // with no assertion behind it. The poll budget the component tests use is
      // `POLL_BUDGET_MS` in `src/test-support/poll-budget.ts`.
      testTimeout: 30_000,
      hookTimeout: 30_000,
    },
  };
});
