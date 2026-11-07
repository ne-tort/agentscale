import type { ApiClient, Cabinet, CurrentUserResponse, Employee, SessionTokens } from "../../api/types";

export const AUTH_STORAGE_KEY = "agentscale.web.session";

export type AuthStatus = "loading" | "authenticated" | "unauthenticated" | "error";
export type AuthEndReason = "expired" | "revoked" | "signed-out";

export interface AuthSnapshot {
  status: AuthStatus;
  tokens?: SessionTokens;
  employee?: Employee;
  cabinets: Cabinet[];
  /**
   * Which contours `/me` says this account belongs to — `platform_admin` is the
   * one the shell branches on. Empty until the first `/me` answers, so a screen
   * must treat "unknown" as "not admin" rather than guessing.
   */
  contours: string[];
  error?: Error;
  endReason?: AuthEndReason;
}

export interface AuthStorage {
  getItem(key: string): string | null;
  setItem(key: string, value: string): void;
  removeItem(key: string): void;
}

type AuthApi = Pick<ApiClient, "me" | "refresh" | "logout">;
type Listener = (snapshot: AuthSnapshot) => void;

function isTokens(value: unknown): value is SessionTokens {
  if (typeof value !== "object" || value === null) return false;
  const candidate = value as Partial<SessionTokens>;
  return typeof candidate.access_token === "string" && candidate.access_token.trim().length > 0
    && typeof candidate.refresh_token === "string" && candidate.refresh_token.trim().length > 0;
}

function isUnauthorized(error: unknown): boolean {
  if (typeof error !== "object" || error === null) return false;
  const value = error as { status?: unknown; error?: { status?: unknown } };
  return value.status === 401 || value.error?.status === 401;
}

function isRevoked(error: unknown): boolean {
  if (typeof error !== "object" || error === null) return false;
  const value = error as { status?: unknown; error?: { status?: unknown } };
  return value.status === 403 || value.error?.status === 403;
}

function asError(_error: unknown): Error {
  return new Error("The service could not verify this session. Check your connection and retry.");
}

function accessTokenExpiry(accessToken: string): number | undefined {
  try {
    const payload = accessToken.split(".")[1];
    if (!payload) return undefined;
    const base64 = payload.replace(/-/g, "+").replace(/_/g, "/").padEnd(Math.ceil(payload.length / 4) * 4, "=");
    const bytes = Uint8Array.from(atob(base64), (character) => character.charCodeAt(0));
    const value: unknown = JSON.parse(new TextDecoder().decode(bytes));
    if (typeof value !== "object" || value === null) return undefined;
    const exp = (value as { exp?: unknown }).exp;
    return typeof exp === "number" && Number.isFinite(exp) ? exp * 1000 : undefined;
  } catch {
    return undefined;
  }
}

/**
 * The employee behind this session, or `undefined` when there is none.
 *
 * A company account signs in as the company: `/me` answers with `employee: null`
 * and `company` filled in. This function used to build an Employee anyway, from
 * the email or the token subject, which made every screen that gates on "is
 * this an employee?" answer yes — and then call employee-only endpoints with a
 * company token and show the 403 it got back.
 */
function employeeFromCurrentUser(user: CurrentUserResponse): Employee | undefined {
  const employeeId = user.employee_id ?? user.employee?.id;
  if (!employeeId && !user.employee) return undefined;
  const identity = employeeId ?? user.id ?? user.subject ?? user.sub ?? "unknown";
  const displayName = user.display_name?.trim() || user.employee?.contact_email || user.employee?.email || user.email?.trim() || identity;
  return {
    id: identity,
    display_name: displayName,
    ...(user.email ? { email: user.email } : {}),
  };
}

/** Owns persisted credentials and serializes restore/refresh work across provider effects. */
export class AuthLifecycle {
  private snapshot: AuthSnapshot = { status: "loading", cabinets: [], contours: [] };
  private readonly listeners = new Set<Listener>();
  private recoveryFlight?: Promise<void>;
  private refreshFlight?: { refreshToken: string; promise: Promise<SessionTokens> };
  private generation = 0;
  private refreshTimer?: ReturnType<typeof setTimeout>;
  private contours: string[] = [];

  constructor(private readonly api: AuthApi, private readonly storage?: AuthStorage | null) {}

  getSnapshot = (): AuthSnapshot => this.snapshot;

  subscribe = (listener: Listener): (() => void) => {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  };

  /** Reads a prior session, validates it with /me, and rotates tokens once if needed. */
  recover(): Promise<void> {
    if (this.recoveryFlight) return this.recoveryFlight;
    if (this.snapshot.status !== "loading" && !(this.snapshot.status === "error" && this.snapshot.tokens)) return Promise.resolve();

    const generation = this.generation;
    let flight!: Promise<void>;
    flight = Promise.resolve().then(() => this.restore(generation)).finally(() => {
      if (this.recoveryFlight === flight) this.recoveryFlight = undefined;
    });
    this.recoveryFlight = flight;
    return flight;
  }

  /** Persists login tokens and resolves the current user without requiring page changes. */
  async signIn(tokens: SessionTokens, employee?: Employee, cabinets: Cabinet[] = []): Promise<void> {
    if (!isTokens(tokens)) throw new TypeError("Invalid session tokens");
    const generation = ++this.generation;
    this.cancelScheduledRefresh();
    this.saveTokens(tokens);
    this.publish({ status: "loading", tokens, employee, cabinets });
    await this.resolveSession(tokens, generation, employee, cabinets);
  }

  /** Clears local state immediately and calls the API's best-effort logout contract. */
  async signOut(): Promise<void> {
    let tokens = this.snapshot.tokens ?? this.readTokens();
    ++this.generation;
    this.cancelScheduledRefresh();
    this.removeTokens();
    // Contours describe the account that just left; the next one may be a
    // completely different role.
    this.contours = [];
    this.publish({ status: "unauthenticated", cabinets: [], endReason: "signed-out" });
    if (!tokens) return;

    const pendingRefresh = this.refreshFlight;
    if (pendingRefresh?.refreshToken === tokens.refresh_token) {
      try {
        const rotated = await pendingRefresh.promise;
        if (isTokens(rotated)) tokens = rotated;
      } catch {
        // A failed refresh does not prevent best-effort revocation of the current credentials.
      }
    }
    try {
      await this.api.logout(tokens);
    } catch {
      // Local logout remains effective even if the server cannot be reached.
    }
  }

  private async restore(generation: number): Promise<void> {
    const tokens = this.readTokens();
    if (!tokens) {
      if (generation === this.generation) this.publish({ status: "unauthenticated", cabinets: [] });
      return;
    }
    this.publish({ status: "loading", tokens, cabinets: [] });
    await this.resolveSession(tokens, generation, undefined, []);
  }

  private async resolveSession(tokens: SessionTokens, generation: number, fallbackEmployee?: Employee, fallbackCabinets: Cabinet[] = []): Promise<void> {
    try {
      const user = await this.api.me();
      this.contours = user.contours ?? [];
      if (generation !== this.generation) return;
      if (!user.active) {
        this.removeTokens();
        this.cancelScheduledRefresh();
        this.publish({ status: "unauthenticated", cabinets: [], endReason: "revoked" });
        return;
      }
      this.publish({ status: "authenticated", tokens, employee: employeeFromCurrentUser(user), cabinets: fallbackCabinets });
      this.scheduleRefresh(tokens, generation);
    } catch (error) {
      if (generation !== this.generation) return;
      if (!isUnauthorized(error)) {
        if (isRevoked(error)) {
          this.removeTokens();
          this.cancelScheduledRefresh();
          this.publish({ status: "unauthenticated", cabinets: [], endReason: "revoked" });
          return;
        }
        this.publish({ status: "error", tokens, employee: fallbackEmployee, cabinets: fallbackCabinets, error: asError(error) });
        return;
      }

      let refreshed: SessionTokens;
      try {
        refreshed = await this.refreshOnce(tokens.refresh_token);
      } catch (refreshError) {
        if (generation !== this.generation) return;
        if (isUnauthorized(refreshError)) {
          this.removeTokens();
          this.cancelScheduledRefresh();
          this.publish({ status: "unauthenticated", cabinets: [], endReason: "expired" });
        } else {
          this.publish({ status: "error", tokens, employee: fallbackEmployee, cabinets: fallbackCabinets, error: asError(refreshError) });
        }
        return;
      }
      if (generation !== this.generation) return;
      if (!isTokens(refreshed)) {
        this.publish({ status: "error", tokens, employee: fallbackEmployee, cabinets: fallbackCabinets, error: new Error("Invalid refresh response") });
        return;
      }
      this.saveTokens(refreshed);
      this.publish({ status: "loading", tokens: refreshed, employee: fallbackEmployee, cabinets: fallbackCabinets });
      try {
        const user = await this.api.me();
        this.contours = user.contours ?? [];
        if (generation !== this.generation) return;
        if (!user.active) {
          this.removeTokens();
          this.cancelScheduledRefresh();
          this.publish({ status: "unauthenticated", cabinets: [], endReason: "revoked" });
          return;
        }
        this.publish({ status: "authenticated", tokens: refreshed, employee: employeeFromCurrentUser(user), cabinets: fallbackCabinets });
        this.scheduleRefresh(refreshed, generation);
      } catch (retryError) {
        if (generation !== this.generation) return;
        if (isUnauthorized(retryError)) {
          this.removeTokens();
          this.cancelScheduledRefresh();
          this.publish({ status: "unauthenticated", cabinets: [], endReason: "expired" });
        } else if (isRevoked(retryError)) {
          this.removeTokens();
          this.cancelScheduledRefresh();
          this.publish({ status: "unauthenticated", cabinets: [], endReason: "revoked" });
        } else {
          this.publish({ status: "error", tokens: refreshed, employee: fallbackEmployee, cabinets: fallbackCabinets, error: asError(retryError) });
        }
      }
    }
  }

  private refreshOnce(refreshToken: string): Promise<SessionTokens> {
    if (this.refreshFlight?.refreshToken === refreshToken) return this.refreshFlight.promise;
    let promise!: Promise<SessionTokens>;
    promise = Promise.resolve().then(() => this.api.refresh(refreshToken)).finally(() => {
      if (this.refreshFlight?.promise === promise) this.refreshFlight = undefined;
    });
    this.refreshFlight = { refreshToken, promise };
    return promise;
  }

  private scheduleRefresh(tokens: SessionTokens, generation: number): void {
    this.cancelScheduledRefresh();
    const expiresAt = accessTokenExpiry(tokens.access_token);
    if (expiresAt === undefined) return;
    const delay = Math.max(5_000, expiresAt - Date.now() - 60_000);
    this.refreshTimer = setTimeout(() => {
      this.refreshTimer = undefined;
      void this.refreshExpiringSession(tokens, generation);
    }, delay);
  }

  private async refreshExpiringSession(tokens: SessionTokens, generation: number): Promise<void> {
    if (generation !== this.generation) return;
    this.publish({ status: "loading", tokens, employee: this.snapshot.employee, cabinets: this.snapshot.cabinets });
    try {
      const refreshed = await this.refreshOnce(tokens.refresh_token);
      if (generation !== this.generation) return;
      if (!isTokens(refreshed)) throw new Error("Invalid refresh response");
      this.saveTokens(refreshed);
      const employee = this.snapshot.employee;
      const cabinets = this.snapshot.cabinets;
      this.publish({ status: "loading", tokens: refreshed, employee, cabinets });
      try {
        const user = await this.api.me();
        this.contours = user.contours ?? [];
        if (generation !== this.generation) return;
        if (!user.active) {
          this.removeTokens();
          this.cancelScheduledRefresh();
          this.publish({ status: "unauthenticated", cabinets: [], endReason: "revoked" });
          return;
        }
        this.publish({ status: "authenticated", tokens: refreshed, employee: employeeFromCurrentUser(user), cabinets });
        this.scheduleRefresh(refreshed, generation);
      } catch (error) {
        if (generation !== this.generation) return;
        if (isUnauthorized(error)) {
          this.removeTokens();
          this.cancelScheduledRefresh();
          this.publish({ status: "unauthenticated", cabinets: [], endReason: "expired" });
        } else if (isRevoked(error)) {
          this.removeTokens();
          this.cancelScheduledRefresh();
          this.publish({ status: "unauthenticated", cabinets: [], endReason: "revoked" });
        } else {
          this.publish({ status: "error", tokens: refreshed, employee, cabinets, error: asError(error) });
        }
      }
    } catch (error) {
      if (generation !== this.generation) return;
      if (isUnauthorized(error)) {
        this.removeTokens();
        this.cancelScheduledRefresh();
        this.publish({ status: "unauthenticated", cabinets: [], endReason: "expired" });
      } else {
        this.publish({
          status: "error",
          tokens,
          employee: this.snapshot.employee,
          cabinets: this.snapshot.cabinets,
          error: asError(error),
        });
      }
    }
  }

  private cancelScheduledRefresh(): void {
    if (this.refreshTimer !== undefined) clearTimeout(this.refreshTimer);
    this.refreshTimer = undefined;
  }

  private readTokens(): SessionTokens | undefined {
    try {
      const raw = this.storage?.getItem(AUTH_STORAGE_KEY);
      if (!raw) return undefined;
      const parsed: unknown = JSON.parse(raw);
      if (isTokens(parsed)) return parsed;
      this.removeTokens();
    } catch {
      this.removeTokens();
    }
    return undefined;
  }

  private saveTokens(tokens: SessionTokens): void {
    try { this.storage?.setItem(AUTH_STORAGE_KEY, JSON.stringify(tokens)); } catch { /* keep this session usable when storage is blocked */ }
  }

  private removeTokens(): void {
    try { this.storage?.removeItem(AUTH_STORAGE_KEY); } catch { /* continue clearing in-memory state */ }
  }

  private publish(snapshot: Omit<AuthSnapshot, "contours">): void {
    // Contours come from `/me`, not from whatever a caller happens to pass, so
    // they cannot be set by a screen and cannot leak across a sign-out.
    this.snapshot = { ...snapshot, contours: this.contours };
    for (const listener of this.listeners) listener(this.snapshot);
  }}
