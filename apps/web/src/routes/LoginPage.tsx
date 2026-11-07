import { useEffect, useRef, useState, type FormEvent, type ReactNode } from "react";
import { Navigate, useLocation } from "react-router-dom";
import { useApi, useAuth } from "../app/context";
import { getSafeNextPath } from "../app/auth/redirect";
import { useSettings } from "../preferences/SettingsProvider";
import type { Locale, ThemeMode } from "../preferences/settings";

/**
 * Dev contour ships a seeded platform admin. Prefilling it makes the page usable
 * without a password manager on first open; a production build can turn it off
 * with `VITE_PREFILL_CREDENTIALS=false`.
 */
const PREFILL = import.meta.env.VITE_PREFILL_CREDENTIALS !== "false";
const DEFAULT_USERNAME = PREFILL ? "admin" : "";
const DEFAULT_PASSWORD = PREFILL ? "admin" : "";

function Icon({ children, size = 24 }: { children: ReactNode; size?: number }) {
  return (
    <svg aria-hidden="true" className="pref-icon" focusable="false" height={size} viewBox="0 0 24 24" width={size}>
      {children}
    </svg>
  );
}

/** Outline gear — the same affordance the Flutter login screen had in its header. */
function SettingsIcon() {
  return (
    <Icon>
      <path
        d="M12 15.2a3.2 3.2 0 1 0 0-6.4 3.2 3.2 0 0 0 0 6.4Z"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.6"
      />
      <path
        d="M19.3 12c0-.5-.06-1-.17-1.47l1.72-1.2-1.9-3.3-1.94.86a7.6 7.6 0 0 0-2.55-1.48L14.1 3.3h-3.8l-.36 2.11a7.6 7.6 0 0 0-2.55 1.48L5.45 6.03l-1.9 3.3 1.72 1.2a7.8 7.8 0 0 0 0 2.94l-1.72 1.2 1.9 3.3 1.94-.86a7.6 7.6 0 0 0 2.55 1.48l.36 2.11h3.8l.36-2.11a7.6 7.6 0 0 0 2.55-1.48l1.94.86 1.9-3.3-1.72-1.2c.11-.47.17-.97.17-1.47Z"
        fill="none"
        stroke="currentColor"
        strokeLinejoin="round"
        strokeWidth="1.6"
      />
    </Icon>
  );
}

function PersonIcon() {
  return (
    <Icon>
      <path
        d="M12 12.5a3.6 3.6 0 1 0 0-7.2 3.6 3.6 0 0 0 0 7.2ZM5.2 19.4c.6-2.9 3.4-4.8 6.8-4.8s6.2 1.9 6.8 4.8"
        fill="none"
        stroke="currentColor"
        strokeLinecap="round"
        strokeWidth="1.6"
      />
    </Icon>
  );
}

function KeyIcon() {
  return (
    <Icon>
      <circle cx="8.6" cy="12" fill="none" r="3.4" stroke="currentColor" strokeWidth="1.6" />
      <path
        d="M12 12h8m-2.4 0v2.6M16.2 12v2.2"
        fill="none"
        stroke="currentColor"
        strokeLinecap="round"
        strokeWidth="1.6"
      />
    </Icon>
  );
}

function EyeIcon({ open }: { open: boolean }) {
  return (
    <svg aria-hidden="true" focusable="false" height="20" viewBox="0 0 24 24" width="20">
      {open ? (
        <>
          <path
            d="M2.6 12S6 5.9 12 5.9 21.4 12 21.4 12 18 18.1 12 18.1 2.6 12 2.6 12Z"
            fill="none"
            stroke="currentColor"
            strokeLinejoin="round"
            strokeWidth="1.6"
          />
          <circle cx="12" cy="12" fill="none" r="2.8" stroke="currentColor" strokeWidth="1.6" />
        </>
      ) : (
        <>
          <path
            d="M2.6 12S6 5.9 12 5.9 21.4 12 21.4 12 18 18.1 12 18.1 2.6 12 2.6 12Z"
            fill="none"
            stroke="currentColor"
            strokeLinejoin="round"
            strokeWidth="1.6"
          />
          <path d="M4 20 20 4" fill="none" stroke="currentColor" strokeLinecap="round" strokeWidth="1.6" />
        </>
      )}
    </svg>
  );
}

/** Rows of choices, matching the Flutter settings list rather than a native select. */
function Choice<T extends string>({
  label,
  value,
  options,
  onChange,
}: {
  label: string;
  value: T;
  options: readonly { value: T; label: string }[];
  onChange: (next: T) => void;
}) {
  return (
    <div className="settings-group">
      <span className="settings-group-label">{label}</span>
      <div aria-label={label} className="settings-choices" role="group">
        {options.map((option) => (
          <button
            aria-pressed={option.value === value}
            className="settings-choice"
            key={option.value}
            onClick={() => onChange(option.value)}
            type="button"
          >
            {option.label}
          </button>
        ))}
      </div>
    </div>
  );
}

function LoginSettings({ onClose }: { onClose: () => void }) {
  const { locale, theme, strings, setLocale, setTheme } = useSettings();
  const panel = useRef<HTMLDivElement>(null);

  useEffect(() => {
    function onKey(event: KeyboardEvent) {
      if (event.key === "Escape") onClose();
    }
    function onPointer(event: MouseEvent) {
      if (panel.current && !panel.current.contains(event.target as Node)) onClose();
    }
    // Capture, so a click on another control closes the menu before that
    // control's own handler runs.
    document.addEventListener("keydown", onKey);
    document.addEventListener("mousedown", onPointer);
    return () => {
      document.removeEventListener("keydown", onKey);
      document.removeEventListener("mousedown", onPointer);
    };
  }, [onClose]);

  const locales: readonly { value: Locale; label: string }[] = [
    { value: "ru", label: strings.languageRu },
    { value: "en", label: strings.languageEn },
  ];
  const themes: readonly { value: ThemeMode; label: string }[] = [
    { value: "system", label: strings.themeSystem },
    { value: "light", label: strings.themeLight },
    { value: "dark", label: strings.themeDark },
  ];

  return (
    <div aria-label={strings.settings} className="settings-panel" ref={panel} role="dialog">
      <Choice label={strings.language} onChange={setLocale} options={locales} value={locale} />
      <Choice label={strings.theme} onChange={setTheme} options={themes} value={theme} />
    </div>
  );
}

type PrefRowProps = {
  title: string;
  icon: ReactNode;
  trailing?: ReactNode;
  children: ReactNode;
};

/** Icon + label + borderless field, the same row the Flutter login screen used. */
function PrefRow({ title, icon, trailing, children }: PrefRowProps) {
  return (
    <div className="pref-row">
      <span className="pref-leading">{icon}</span>
      <div className="pref-body">
        <span className="pref-title">{title}</span>
        {children}
      </div>
      {trailing && <span className="pref-trailing">{trailing}</span>}
    </div>
  );
}

export function LoginPage() {
  const api = useApi();
  const auth = useAuth();
  const location = useLocation();
  const { strings } = useSettings();
  const [username, setUsername] = useState(DEFAULT_USERNAME);
  const [password, setPassword] = useState(DEFAULT_PASSWORD);
  const [error, setError] = useState<string>();
  const [busy, setBusy] = useState(false);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [reveal, setReveal] = useState(false);
  const passwordField = useRef<HTMLInputElement>(null);
  const next = getSafeNextPath(location.search) ?? "/projects";

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (busy || !username.trim() || !password) return;
    setBusy(true);
    setError(undefined);
    try {
      const tokens = await api.login(username.trim(), password);
      auth.signIn(tokens);
    } catch (cause) {
      const message = cause instanceof Error ? cause.message : "";
      // The API answers English problem+json; the page speaks the chosen locale.
      setError(/invalid (user )?credentials/i.test(message) ? strings.invalidCredentials : strings.signInFailed);
    } finally {
      setBusy(false);
    }
  }

  if (auth.isAuthenticated) return <Navigate to={next} replace />;

  return (
    <main className="login-page">
      <section className="login-card">
        <header className="login-header">
          <h1 className="login-title">{strings.signIn}</h1>
          <button
            aria-expanded={settingsOpen}
            aria-haspopup="dialog"
            aria-label={strings.settings}
            className="icon-button"
            onClick={() => setSettingsOpen((open) => !open)}
            title={strings.settings}
            type="button"
          >
            <SettingsIcon />
          </button>
          {settingsOpen && <LoginSettings onClose={() => setSettingsOpen(false)} />}
        </header>

        <form className="login-form" onSubmit={(event) => void submit(event)}>
          <PrefRow icon={<PersonIcon />} title={strings.login}>
            <input
              aria-label={strings.login}
              autoComplete="username"
              autoFocus
              className="pref-input"
              disabled={busy}
              onChange={(event) => setUsername(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === "Enter") {
                  event.preventDefault();
                  passwordField.current?.focus();
                }
              }}
              placeholder={strings.loginHint}
              spellCheck={false}
              type="text"
              value={username}
            />
          </PrefRow>

          <PrefRow
            icon={<KeyIcon />}
            title={strings.password}
            trailing={
              <button
                aria-label={reveal ? strings.hidePassword : strings.showPassword}
                className="icon-button"
                onClick={() => setReveal((value) => !value)}
                tabIndex={-1}
                title={reveal ? strings.hidePassword : strings.showPassword}
                type="button"
              >
                <EyeIcon open={!reveal} />
              </button>
            }
          >
            <input
              aria-label={strings.password}
              autoComplete="current-password"
              className="pref-input"
              disabled={busy}
              onChange={(event) => setPassword(event.target.value)}
              ref={passwordField}
              type={reveal ? "text" : "password"}
              value={password}
            />
          </PrefRow>

          {error && <p className="error" role="alert">{error}</p>}

          <button className="primary login-submit" disabled={busy} type="submit">
            {busy ? strings.signingIn : strings.signIn}
          </button>
        </form>
      </section>
    </main>
  );
}
