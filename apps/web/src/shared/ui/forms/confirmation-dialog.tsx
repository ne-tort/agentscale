import { useEffect, useId, useRef, type KeyboardEvent, type ReactNode } from "react";

export interface ConfirmationDialogProps {
  readonly open: boolean;
  readonly title: string;
  readonly description?: ReactNode;
  readonly children?: ReactNode;
  readonly confirmLabel?: string;
  readonly cancelLabel?: string;
  readonly confirmVariant?: "primary" | "danger";
  readonly busy?: boolean;
  readonly closeOnBackdrop?: boolean;
  readonly onConfirm: () => void;
  readonly onCancel: () => void;
}

export function ConfirmationDialog({
  open,
  title,
  description,
  children,
  confirmLabel = "Confirm",
  cancelLabel = "Cancel",
  confirmVariant = "danger",
  busy = false,
  closeOnBackdrop = false,
  onConfirm,
  onCancel,
}: ConfirmationDialogProps) {
  const generatedId = useId();
  const titleId = `${generatedId}-title`;
  const descriptionId = description ? `${generatedId}-description` : undefined;
  const cancelRef = useRef<HTMLButtonElement>(null);
  const dialogRef = useRef<HTMLElement>(null);
  const scrimRef = useRef<HTMLDivElement>(null);
  const previouslyFocusedRef = useRef<HTMLElement | null>(null);

  useEffect(() => {
    if (!open) return undefined;
    previouslyFocusedRef.current = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    const scrim = scrimRef.current;
    const outsideElements: HTMLElement[] = [];
    let branch: HTMLElement | null = scrim;
    while (branch?.parentElement && branch !== document.body) {
      for (const sibling of Array.from(branch.parentElement.children)) {
        if (sibling instanceof HTMLElement && sibling !== branch) outsideElements.push(sibling);
      }
      branch = branch.parentElement;
    }
    const previousSiblings = Array.from(new Set(outsideElements))
      .map((element) => ({ element, inert: element.inert, ariaHidden: element.getAttribute("aria-hidden") }));
    for (const { element } of previousSiblings) {
      element.inert = true;
      element.setAttribute("aria-hidden", "true");
    }
    cancelRef.current?.focus();
    const keepFocusInside = (event: FocusEvent) => {
      if (event.target instanceof Node && scrim?.contains(event.target)) return;
      if (cancelRef.current?.disabled) dialogRef.current?.focus();
      else cancelRef.current?.focus();
    };
    document.addEventListener("focusin", keepFocusInside);
    return () => {
      document.removeEventListener("focusin", keepFocusInside);
      for (const { element, inert, ariaHidden } of previousSiblings) {
        element.inert = inert;
        if (ariaHidden === null) element.removeAttribute("aria-hidden");
        else element.setAttribute("aria-hidden", ariaHidden);
      }
      previouslyFocusedRef.current?.focus();
    };
  }, [open]);

  useEffect(() => {
    if (open && busy) dialogRef.current?.focus();
  }, [busy, open]);

  const handleKeyDown = (event: KeyboardEvent<HTMLElement>) => {
    if (event.key === "Escape" && !busy) {
      event.preventDefault();
      onCancel();
      return;
    }
    if (event.key !== "Tab") return;

    const controls = Array.from(event.currentTarget.querySelectorAll<HTMLElement>(
      'button:not(:disabled), a[href], input:not(:disabled):not([type="hidden"]), select:not(:disabled), textarea:not(:disabled), [tabindex]:not([tabindex="-1"])',
    )).filter((control) => control.getAttribute("aria-hidden") !== "true" && !control.closest("[hidden], [inert], [aria-hidden='true']"));
    if (controls.length === 0) {
      event.preventDefault();
      dialogRef.current?.focus();
      return;
    }
    const first = controls[0];
    const last = controls[controls.length - 1];
    if (event.shiftKey && (document.activeElement === first || document.activeElement === event.currentTarget)) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && (document.activeElement === last || document.activeElement === event.currentTarget)) {
      event.preventDefault();
      first.focus();
    }
  };

  if (!open) return null;

  return <div className="form-dialog-scrim" ref={scrimRef}>
    <div
      aria-hidden="true"
      className="form-dialog-backdrop"
      onClick={() => { if (closeOnBackdrop && !busy) onCancel(); }}
    />
    <section
      aria-busy={busy || undefined}
      aria-describedby={descriptionId}
      aria-labelledby={titleId}
      aria-modal="true"
      className="form-dialog"
      onKeyDown={handleKeyDown}
      ref={dialogRef}
      role="alertdialog"
      tabIndex={-1}
    >
      <h2 className="form-dialog__title" id={titleId}>{title}</h2>
      {description && <p className="form-dialog__description" id={descriptionId}>{description}</p>}
      {children && <div className="form-dialog__content">{children}</div>}
      <div className="form-dialog__actions">
        <button className="form-button form-button--secondary" disabled={busy} onClick={onCancel} ref={cancelRef} type="button">{cancelLabel}</button>
        <button className={`form-button form-button--${confirmVariant}`} disabled={busy} onClick={onConfirm} type="button">
          {busy && <span aria-hidden="true" className="form-spinner" />}
          {confirmLabel}
        </button>
      </div>
    </section>
  </div>;
}