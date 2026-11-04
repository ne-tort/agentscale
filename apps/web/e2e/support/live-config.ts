/**
 * Live-layer configuration, read from the environment when the config loads.
 *
 * There is deliberately no default account: the live layer must be skipped, not
 * guessed, when an operator has not provisioned one. `e2e/README.md` documents the
 * provisioning recipe that produces these values.
 */

export interface LiveCredentials {
  readonly apiUrl: string;
  readonly username: string;
  readonly password: string;
}

function clean(value: string | undefined): string | undefined {
  const trimmed = value?.trim();
  return trimmed ? trimmed : undefined;
}

const apiUrl = clean(process.env.E2E_API_URL);
const username = clean(process.env.E2E_USERNAME);
const password = clean(process.env.E2E_PASSWORD);

/** All three variables are required together; a partial set skips with a reason. */
export const E2E_LIVE_CREDENTIALS: LiveCredentials | undefined =
  apiUrl && username && password ? { apiUrl, username, password } : undefined;

export const LIVE_SKIP_REASON =
  "live layer needs E2E_API_URL, E2E_USERNAME and E2E_PASSWORD (see apps/web/e2e/README.md)";
