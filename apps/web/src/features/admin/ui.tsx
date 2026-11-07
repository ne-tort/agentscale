import { useCallback, useEffect, useRef, useState, type FormEvent, type ReactNode } from "react";
import { useI18n } from "../../i18n/useT";

/**
 * Loads a list and keeps it around while it reloads.
 *
 * `reload` is passed to handlers so a create/delete can refresh the table in
 * place, without the whole page dropping back to a skeleton.
 */
export function useCollection<T>(load: () => Promise<T[]>, dependencies: unknown[]) {
  const [rows, setRows] = useState<T[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string>();

  const reload = useCallback(async () => {
    setError(undefined);
    try {
      setRows(await load());
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : undefined);
    } finally {
      setLoading(false);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, dependencies);

  useEffect(() => {
    void reload();
  }, [reload]);

  return { rows, loading, error, reload };
}

export function Notice({ error, success }: { error?: string; success?: string }) {
  if (!error && !success) return null;
  return (
    <>
      {error && (
        <p className="admin-notice admin-notice--error" role="alert">
          {error}
        </p>
      )}
      {success && (
        <p className="admin-notice admin-notice--success" role="status">
          {success}
        </p>
      )}
    </>
  );
}

/** Centred dialog. Escape and a click on the backdrop close it; focus starts inside. */
export function Modal({
  title,
  detail,
  onClose,
  children,
  footer,
}: {
  title: string;
  detail?: string;
  onClose: () => void;
  children: ReactNode;
  footer?: ReactNode;
}) {
  const panel = useRef<HTMLDivElement>(null);
  const { t } = useI18n();

  useEffect(() => {
    function onKey(event: KeyboardEvent) {
      if (event.key === "Escape") onClose();
    }
    document.addEventListener("keydown", onKey);
    const first = panel.current?.querySelector<HTMLElement>(
      "input, select, textarea, button:not([data-dismiss])",
    );
    first?.focus();
    return () => document.removeEventListener("keydown", onKey);
  }, [onClose]);

  return (
    <div className="admin-backdrop" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose(); }}>
      <div aria-label={title} aria-modal="true" className="admin-modal" ref={panel} role="dialog">
        <header className="admin-modal__header">
          <div>
            <h2>{title}</h2>
            {detail && <p className="admin-muted">{detail}</p>}
          </div>
          <button aria-label={t("common.close")} className="admin-icon-button" data-dismiss onClick={onClose} type="button">
            ×
          </button>
        </header>
        <div className="admin-modal__body">{children}</div>
        {footer && <footer className="admin-modal__footer">{footer}</footer>}
      </div>
    </div>
  );
}

export function Field({ label, hint, children }: { label: string; hint?: string; children: ReactNode }) {
  return (
    <label className="admin-field">
      <span className="admin-field__label">{label}</span>
      {children}
      {hint && <span className="admin-field__hint">{hint}</span>}
    </label>
  );
}

/**
 * A destructive action that asks once, inline, instead of opening a dialog.
 *
 * The confirmation is the last chance to see the request fail, so a rejection is
 * shown right here and the row stays in its asking state: every caller used to
 * write `void service.delete(...).then(...)`, which turned a 403 into a click
 * that did nothing at all.
 */
export function ConfirmButton({
  label,
  confirmLabel,
  onConfirm,
  disabled,
  disabledReason,
}: {
  label: string;
  confirmLabel: string;
  onConfirm: () => void | Promise<void>;
  disabled?: boolean;
  /** Shown as the button's tooltip when it is disabled. */
  disabledReason?: string;
}) {
  const [asking, setAsking] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string>();
  const { t } = useI18n();

  if (disabled) {
    return (
      <button className="admin-button admin-button--danger" disabled title={disabledReason} type="button">
        {label}
      </button>
    );
  }

  async function confirm() {
    setBusy(true);
    setError(undefined);
    try {
      await onConfirm();
      setAsking(false);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : t("common.error"));
    } finally {
      setBusy(false);
    }
  }

  if (!asking) {
    return (
      <button className="admin-button admin-button--danger" onClick={() => setAsking(true)} type="button">
        {label}
      </button>
    );
  }
  return (
    <span className="admin-confirm">
      <span>{confirmLabel}</span>
      <button className="admin-button admin-button--danger" disabled={busy} onClick={() => void confirm()} type="button">
        {busy ? t("common.saving") : label}
      </button>
      <button className="admin-button" disabled={busy} onClick={() => setAsking(false)} type="button">
        {t("common.cancel")}
      </button>
      {error && (
        <span className="admin-confirm__error" role="alert">
          {error}
        </span>
      )}
    </span>
  );
}

/**
 * Search field + row count + the list's own primary action, on one line.
 *
 * The action used to sit in a section header above the table, next to a heading
 * that repeated the tab name. Moving it here puts "find a company" and "add a
 * company" at the same level — the two things a person does to this list — and
 * lets the page start with content instead of a title block.
 */
export function ListToolbar({
  query,
  onQuery,
  count,
  action,
  label,
}: {
  query: string;
  onQuery: (value: string) => void;
  count: number;
  /** The list's primary action (`Создать …`). */
  action?: ReactNode;
  /** Replaces the generic row count, e.g. `plural("admin.companiesRecord", n)`. */
  label?: string;
}) {
  const { t, plural } = useI18n();
  const countLabel = label ?? plural("common.records", count);
  return (
    <div className="admin-toolbar">
      <input
        aria-label={t("common.search")}
        className="admin-search"
        onChange={(event) => onQuery(event.target.value)}
        placeholder={t("common.search")}
        type="search"
        value={query}
      />
      <span className="admin-muted admin-toolbar__count">{countLabel}</span>
      {action && <div className="admin-toolbar__action">{action}</div>}
    </div>
  );
}

export function DialogForm({
  onSubmit,
  submitLabel,
  busy,
  children,
}: {
  onSubmit: (event: FormEvent<HTMLFormElement>) => void;
  submitLabel: string;
  busy: boolean;
  children: ReactNode;
}) {
  const { t } = useI18n();
  return (
    <form className="admin-form" onSubmit={onSubmit}>
      <div className="admin-form__fields">{children}</div>
      <button className="admin-button admin-button--primary" disabled={busy} type="submit">
        {busy ? t("common.saving") : submitLabel}
      </button>
    </form>
  );
}
