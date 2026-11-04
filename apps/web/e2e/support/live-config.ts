/**
 * Live-layer configuration, read from the environment at load time.
 *
 * Nothing here has a default credential: the live layer is skipped unless the
 * operator explicitly exports a working login. See `e2e/README.md` for the
 * provisioning recipe (`platform admin` → company → employee invitation).
 */

function clean(value: string | undefined): string | undefined {
  const trimmed = value?.trim();
  return trimmed ? trimmed : undefined;
}

/** Base URL of a deployed API, e.g. `http://172.31.156.203:8088/api/v1`. */
export const LIVE_API_URL = clean(process.env.E2E_API_URL);

export const LIVE_USERNAME = clean(process.env.E2E_USERNAME);

export const LIVE_PASSWORD = clean(process.env.E2E_PASSWORD);

/**
 * True only when the whole live layer is runnable. Checked per `describe` so a
 * missing variable yields `skipped`, never a red suite or a silent pass.
 */
export const LIVE_READY = Boolean(LIVE_API_URL && LIVE_USERNAME && LIVE_PASSWORD);

/** Human-readable reason used in `test.skip()` so the report explains itself. */
export const LIVE_SKIP_REASON =
  "Live layer requires E2E_API_URL, E2E_USERNAME and E2E_PASSWORD (see apps/web/e2e/README.md)";
