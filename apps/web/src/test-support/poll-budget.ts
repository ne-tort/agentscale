/**
 * How long a component test may keep polling for a committed render.
 *
 * The self-hosted runner is a single VM that also runs the API suite (Postgres
 * plus ~2000 tests) and the Flutter analyzer in the same workflow run, so the
 * CPU is regularly oversubscribed. A poll budget that is generous on a laptop
 * expires there first: the acceptance suite failed the gate on a three-second
 * deadline with no failing assertion anywhere — every "Timed out waiting for
 * the acceptance condition" is a machine being busy, not a broken screen.
 *
 * This bounds *waiting*, not what the tests assert. A real hang still fails,
 * only later; the matching machine-level budget is `testTimeout` in
 * `vite.config.ts`.
 */
export const POLL_BUDGET_MS = 15_000;
