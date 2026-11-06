import { beforeEach, describe, expect, it, vi } from "vitest";
import type { CurrentUserResponse, SessionTokens } from "../../api/types";
import { AUTH_STORAGE_KEY, AuthLifecycle, type AuthStorage } from "./lifecycle";
import { getSafeNextPath } from "./redirect";

const savedTokens: SessionTokens = { access_token: "access-old", refresh_token: "refresh-old" };
const rotatedTokens: SessionTokens = { access_token: "access-new", refresh_token: "refresh-new" };
const currentUser: CurrentUserResponse = {
  id: "user-1", subject: "user@example.test", email: "user@example.test", display_name: "Test User",
  employee_id: "employee-1", roles: ["user"], active: true,
};

class MemoryStorage implements AuthStorage {
  private values = new Map<string, string>();
  getItem(key: string): string | null { return this.values.get(key) ?? null; }
  setItem(key: string, value: string): void { this.values.set(key, value); }
  removeItem(key: string): void { this.values.delete(key); }
}

function unauthorized(): Error & { error: { status: number } } {
  return Object.assign(new Error("Unauthorized"), { error: { status: 401 } });
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}

describe("AuthLifecycle", () => {
  let storage: MemoryStorage;

  beforeEach(() => { storage = new MemoryStorage(); });

  it("restores a persisted session and shares one in-flight recovery", async () => {
    storage.setItem(AUTH_STORAGE_KEY, JSON.stringify(savedTokens));
    const pendingUser = deferred<CurrentUserResponse>();
    const api = { me: vi.fn(() => pendingUser.promise), refresh: vi.fn(), logout: vi.fn() };
    const auth = new AuthLifecycle(api as never, storage);

    const first = auth.recover();
    const second = auth.recover();
    await Promise.resolve();
    expect(api.me).toHaveBeenCalledTimes(1);
    pendingUser.resolve(currentUser);
    await Promise.all([first, second]);

    expect(auth.getSnapshot()).toMatchObject({ status: "authenticated", tokens: savedTokens, employee: { id: "employee-1", display_name: "Test User" } });
    expect(api.refresh).not.toHaveBeenCalled();
  });

  it("resolves the backend /me shape with sub and nested employee but no top-level id", async () => {
    storage.setItem(AUTH_STORAGE_KEY, JSON.stringify(savedTokens));
    const backendUser = {
      sub: "keycloak-subject-1",
      email: "user@example.test",
      roles: ["employee"],
      contours: ["employee"],
      active: true,
      company: null,
      employee: { id: "employee-1", status: "active", email: "user@example.test", contact_email: null, memberships: [] },
      work_context: { cabinet_id: null, project_id: null },
    } as unknown as CurrentUserResponse;
    const api = { me: vi.fn().mockResolvedValue(backendUser), refresh: vi.fn(), logout: vi.fn() };
    const auth = new AuthLifecycle(api as never, storage);

    await auth.recover();

    expect(auth.getSnapshot()).toMatchObject({
      status: "authenticated",
      employee: { id: "employee-1", display_name: "user@example.test", email: "user@example.test" },
    });
  });

  it("shares recovery and refresh work when StrictMode replays provider effects", async () => {
    storage.setItem(AUTH_STORAGE_KEY, JSON.stringify(savedTokens));
    const api = {
      me: vi.fn().mockRejectedValueOnce(unauthorized()).mockResolvedValueOnce(currentUser),
      refresh: vi.fn().mockResolvedValue(rotatedTokens),
      logout: vi.fn(),
    };
    const auth = new AuthLifecycle(api as never, storage);

    // React StrictMode runs setup -> cleanup -> setup before the request settles.
    const firstRecovery = auth.recover();
    const strictModeReplay = auth.recover();
    await Promise.resolve();
    expect(api.me).toHaveBeenCalledTimes(1);
    await Promise.all([firstRecovery, strictModeReplay]);

    expect(api.me).toHaveBeenCalledTimes(2);
    expect(api.refresh).toHaveBeenCalledTimes(1);
    expect(api.refresh).toHaveBeenCalledWith("refresh-old");
    expect(JSON.parse(storage.getItem(AUTH_STORAGE_KEY) ?? "null")).toEqual(rotatedTokens);
    expect(auth.getSnapshot()).toMatchObject({ status: "authenticated", tokens: rotatedTokens });
  });

  it("clears persisted session after refresh is rejected", async () => {
    storage.setItem(AUTH_STORAGE_KEY, JSON.stringify(savedTokens));
    const api = {
      me: vi.fn().mockRejectedValue(unauthorized()),
      refresh: vi.fn().mockRejectedValue(unauthorized()),
      logout: vi.fn(),
    };
    const auth = new AuthLifecycle(api as never, storage);

    await auth.recover();

    expect(api.me).toHaveBeenCalledTimes(1);
    expect(api.refresh).toHaveBeenCalledTimes(1);
    expect(storage.getItem(AUTH_STORAGE_KEY)).toBeNull();
    expect(auth.getSnapshot()).toMatchObject({ status: "unauthenticated", cabinets: [], endReason: "expired" });
  });

  it("reports transient restore errors without discarding the persisted credentials", async () => {
    storage.setItem(AUTH_STORAGE_KEY, JSON.stringify(savedTokens));
    const api = { me: vi.fn().mockRejectedValue(new Error("network unavailable")), refresh: vi.fn(), logout: vi.fn() };
    const auth = new AuthLifecycle(api as never, storage);

    await auth.recover();

    expect(auth.getSnapshot()).toMatchObject({ status: "error", tokens: savedTokens, error: expect.any(Error) });
    expect(auth.getSnapshot().error?.message).toBe("The service could not verify this session. Check your connection and retry.");
    expect(storage.getItem(AUTH_STORAGE_KEY)).toBe(JSON.stringify(savedTokens));
    expect(api.refresh).not.toHaveBeenCalled();
  });

  it("recovers a transient /me outage on explicit retry without dropping the saved session", async () => {
    storage.setItem(AUTH_STORAGE_KEY, JSON.stringify(savedTokens));
    const api = {
      me: vi.fn().mockRejectedValueOnce(new Error("network unavailable")).mockResolvedValueOnce(currentUser),
      refresh: vi.fn(),
      logout: vi.fn(),
    };
    const auth = new AuthLifecycle(api as never, storage);

    await auth.recover();
    expect(auth.getSnapshot()).toMatchObject({ status: "error", tokens: savedTokens });
    expect(storage.getItem(AUTH_STORAGE_KEY)).toBe(JSON.stringify(savedTokens));

    await auth.recover();

    expect(api.me).toHaveBeenCalledTimes(2);
    expect(api.refresh).not.toHaveBeenCalled();
    expect(auth.getSnapshot()).toMatchObject({ status: "authenticated", tokens: savedTokens });
  });

  it("marks an inactive /me identity revoked and removes its local credentials", async () => {
    storage.setItem(AUTH_STORAGE_KEY, JSON.stringify(savedTokens));
    const api = { me: vi.fn().mockResolvedValue({ ...currentUser, active: false }), refresh: vi.fn(), logout: vi.fn() };
    const auth = new AuthLifecycle(api as never, storage);

    await auth.recover();

    expect(storage.getItem(AUTH_STORAGE_KEY)).toBeNull();
    expect(auth.getSnapshot()).toMatchObject({ status: "unauthenticated", endReason: "revoked", cabinets: [] });
    expect(api.refresh).not.toHaveBeenCalled();
  });

  it("treats the backend /me 403 for a disabled employee as revoked", async () => {
    storage.setItem(AUTH_STORAGE_KEY, JSON.stringify(savedTokens));
    const forbidden = Object.assign(new Error("forbidden"), { error: { status: 403 } });
    const api = { me: vi.fn().mockRejectedValue(forbidden), refresh: vi.fn(), logout: vi.fn() };
    const auth = new AuthLifecycle(api as never, storage);

    await auth.recover();

    expect(storage.getItem(AUTH_STORAGE_KEY)).toBeNull();
    expect(auth.getSnapshot()).toMatchObject({ status: "unauthenticated", endReason: "revoked" });
    expect(api.refresh).not.toHaveBeenCalled();
  });

  it("reports a transient refresh outage while retaining credentials for retry", async () => {
    storage.setItem(AUTH_STORAGE_KEY, JSON.stringify(savedTokens));
    const api = {
      me: vi.fn().mockRejectedValueOnce(unauthorized()).mockRejectedValueOnce(unauthorized()).mockResolvedValue(currentUser),
      refresh: vi.fn().mockRejectedValueOnce(new Error("network unavailable")).mockResolvedValue(rotatedTokens),
      logout: vi.fn(),
    };
    const auth = new AuthLifecycle(api as never, storage);

    await auth.recover();

    expect(auth.getSnapshot()).toMatchObject({ status: "error", tokens: savedTokens });
    expect(storage.getItem(AUTH_STORAGE_KEY)).toBe(JSON.stringify(savedTokens));

    await auth.recover();

    expect(api.refresh).toHaveBeenCalledTimes(2);
    expect(auth.getSnapshot()).toMatchObject({ status: "authenticated", tokens: rotatedTokens });
    expect(JSON.parse(storage.getItem(AUTH_STORAGE_KEY) ?? "null")).toEqual(rotatedTokens);
  });

  it("does not let an in-flight recovery restore a session after sign-out", async () => {
    storage.setItem(AUTH_STORAGE_KEY, JSON.stringify(savedTokens));
    const pendingUser = deferred<CurrentUserResponse>();
    const api = { me: vi.fn(() => pendingUser.promise), refresh: vi.fn(), logout: vi.fn().mockResolvedValue(undefined) };
    const auth = new AuthLifecycle(api as never, storage);

    const recovery = auth.recover();
    await Promise.resolve();
    expect(api.me).toHaveBeenCalledTimes(1);
    const logout = auth.signOut();
    expect(auth.getSnapshot()).toMatchObject({ status: "unauthenticated" });
    expect(storage.getItem(AUTH_STORAGE_KEY)).toBeNull();

    pendingUser.resolve(currentUser);
    await Promise.all([recovery, logout]);

    expect(auth.getSnapshot()).toMatchObject({ status: "unauthenticated" });
    expect(api.logout).toHaveBeenCalledWith(savedTokens);
  });

  it("clears local credentials before calling logout with the token contract", async () => {
    storage.setItem(AUTH_STORAGE_KEY, JSON.stringify(savedTokens));
    const api = { me: vi.fn().mockResolvedValue(currentUser), refresh: vi.fn(), logout: vi.fn().mockResolvedValue(undefined) };
    const auth = new AuthLifecycle(api as never, storage);
    await auth.recover();

    await auth.signOut();

    expect(auth.getSnapshot()).toMatchObject({ status: "unauthenticated", cabinets: [] });
    expect(storage.getItem(AUTH_STORAGE_KEY)).toBeNull();
    expect(api.logout).toHaveBeenCalledWith(savedTokens);
    expect(auth.getSnapshot()).toMatchObject({ status: "unauthenticated", endReason: "signed-out" });
  });

  it("keeps local logout effective when the revoke endpoint is unreachable", async () => {
    storage.setItem(AUTH_STORAGE_KEY, JSON.stringify(savedTokens));
    const api = { me: vi.fn().mockResolvedValue(currentUser), refresh: vi.fn(), logout: vi.fn().mockRejectedValue(new Error("offline")) };
    const auth = new AuthLifecycle(api as never, storage);
    await auth.recover();

    await expect(auth.signOut()).resolves.toBeUndefined();

    expect(storage.getItem(AUTH_STORAGE_KEY)).toBeNull();
    expect(auth.getSnapshot()).toMatchObject({ status: "unauthenticated", endReason: "signed-out" });
  });

  it("refreshes shortly before JWT expiry and validates the rotated token with /me", async () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2027-01-01T00:00:00.000Z"));
    const encode = (value: unknown) => btoa(JSON.stringify(value)).replace(/=/g, "").replace(/\+/g, "-").replace(/\//g, "_");
    const expiringTokens = { ...savedTokens, access_token: `e30.${encode({ exp: Date.now() / 1000 + 120 })}.sig` };
    const nextTokens = { ...rotatedTokens, access_token: `e30.${encode({ exp: Date.now() / 1000 + 3600 })}.sig` };
    storage.setItem(AUTH_STORAGE_KEY, JSON.stringify(expiringTokens));
    const api = {
      me: vi.fn().mockResolvedValue(currentUser),
      refresh: vi.fn().mockResolvedValue(nextTokens),
      logout: vi.fn(),
    };
    const auth = new AuthLifecycle(api as never, storage);
    try {
      await auth.recover();
      await vi.advanceTimersByTimeAsync(59_000);
      expect(api.refresh).not.toHaveBeenCalled();

      await vi.advanceTimersByTimeAsync(1_000);

      expect(api.refresh).toHaveBeenCalledTimes(1);
      expect(api.refresh).toHaveBeenCalledWith("refresh-old");
      expect(api.me).toHaveBeenCalledTimes(2);
      expect(auth.getSnapshot()).toMatchObject({ status: "authenticated", tokens: nextTokens });
      expect(JSON.parse(storage.getItem(AUTH_STORAGE_KEY) ?? "null")).toEqual(nextTokens);
    } finally {
      vi.useRealTimers();
    }
  });
});

describe("login return target", () => {
  it("preserves same-origin deep links including search and hash", () => {
    expect(getSafeNextPath("?next=%2Fprojects%2Fp-1%2Fchat%2Fs-2%3Ftab%3Dhistory%23latest"))
      .toBe("/projects/p-1/chat/s-2?tab=history#latest");
  });

  it.each(["https://evil.example", "//evil.example/path", "/\\evil.example", "/login"]) (
    "rejects unsafe return target %s",
    (next) => expect(getSafeNextPath(`?next=${encodeURIComponent(next)}`)).toBeUndefined(),
  );
});
