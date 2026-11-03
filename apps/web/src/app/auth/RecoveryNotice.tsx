import { useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import type { AuthSnapshot } from "./lifecycle";
import { getSafeNextPath } from "./redirect";
import "./recovery.css";

interface RecoveryNoticeProps {
  snapshot: AuthSnapshot;
  retry(): Promise<void>;
  signOut(): Promise<void>;
}

function loginUrl(target: string): string {
  const safeTarget = getSafeNextPath(`?next=${encodeURIComponent(target)}`) ?? "/projects";
  return `/login?next=${encodeURIComponent(safeTarget)}`;
}

function message(snapshot: AuthSnapshot): { title: string; detail: string } {
  if (snapshot.status === "error") {
    return {
      title: "Session check could not reach the service",
      detail: "Your saved sign-in is still on this device. Check your connection and retry, or sign out and choose an account again.",
    };
  }
  if (snapshot.endReason === "revoked") {
    return { title: "This account is no longer active", detail: "Access for this account was disabled. Sign in with another account to continue." };
  }
  if (snapshot.endReason === "signed-out") {
    return { title: "You are signed out", detail: "Sign in again to return to the page you were viewing." };
  }
  return { title: "Your session has expired", detail: "Sign in again to continue. Your current page will be restored after login." };
}

export function RecoveryNotice({ snapshot, retry, signOut }: RecoveryNoticeProps) {
  const navigate = useNavigate();
  const location = useLocation();
  const [busy, setBusy] = useState(false);
  const [actionError, setActionError] = useState<string>();

  if (snapshot.status === "authenticated" || snapshot.status === "loading") return null;
  const copy = message(snapshot);

  async function recoverSession(): Promise<void> {
    setBusy(true);
    setActionError(undefined);
    try {
      await retry();
    } catch {
      setActionError("Session recovery failed. Check your connection and retry.");
    } finally {
      setBusy(false);
    }
  }

  async function switchAccount(): Promise<void> {
    setBusy(true);
    setActionError(undefined);
    const target = location.pathname === "/login"
      ? getSafeNextPath(location.search) ?? "/projects"
      : `${location.pathname}${location.search}${location.hash}`;
    try {
      void signOut().catch(() => undefined);
      navigate(loginUrl(target), { replace: true });
    } catch {
      // The lifecycle clears local state synchronously; account selection remains available offline.
      navigate(loginUrl(target), { replace: true });
    } finally {
      setBusy(false);
    }
  }

  return <section className="auth-recovery" role={snapshot.status === "error" ? "alert" : "status"} aria-live="polite">
    <div className="auth-recovery__copy">
      <strong>{copy.title}</strong>
      <span>{copy.detail}</span>
      {snapshot.status === "error" && snapshot.error ? <span className="auth-recovery__cause">{snapshot.error.message}</span> : null}
      {actionError ? <span role="alert" className="auth-recovery__action-error">{actionError}</span> : null}
    </div>
    <div className="auth-recovery__actions">
      {snapshot.status === "error" ? <button type="button" disabled={busy} onClick={() => void recoverSession()}>
        {busy ? "Checking…" : "Retry session"}
      </button> : null}
      <button type="button" disabled={busy} className="auth-recovery__primary" onClick={() => void switchAccount()}>
        {busy ? "Signing out…" : "Sign in / switch account"}
      </button>
    </div>
  </section>;
}