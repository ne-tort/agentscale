import { useEffect, useId, useRef, type KeyboardEvent } from "react";
import { Button } from "./actions";

export interface ConfirmationDialogProps {
  readonly open: boolean;
  readonly title: string;
  readonly description: string;
  readonly confirmLabel?: string;
  readonly cancelLabel?: string;
  readonly confirmVariant?: "primary" | "danger";
  readonly busy?: boolean;
  readonly onConfirm: () => void;
  readonly onCancel: () => void;
}

export function ConfirmationDialog({
  open,
  title,
  description,
  confirmLabel = "Confirm",
  cancelLabel = "Cancel",
  confirmVariant = "danger",
  busy = false,
  onConfirm,
  onCancel,
}: ConfirmationDialogProps) {
  const generatedId = useId();
  const titleId = `${generatedId}-title`;
  const descriptionId = `${generatedId}-description`;
  const cancelRef = useRef<HTMLButtonElement>(null);
  const confirmRef = useRef<HTMLButtonElement>(null);
  const dialogRef = useRef<HTMLElement>(null);

  useEffect(() => {
    if (!open) return undefined;
    const previouslyFocused = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    cancelRef.current?.focus();
    return () => previouslyFocused?.focus();
  }, [open]);

  useEffect(() => {
    if (open && busy) dialogRef.current?.focus();
  }, [busy, open]);

  if (!open) return null;

  const handleKeyDown = (event: KeyboardEvent<HTMLElement>) => {
    if (event.key === "Escape" && !busy) {
      event.preventDefault();
      onCancel();
      return;
    }
    if (event.key !== "Tab") return;

    const controls = [cancelRef.current, confirmRef.current].filter((control): control is HTMLButtonElement => Boolean(control && !control.disabled));
    if (controls.length === 0) {
      event.preventDefault();
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

  return <div className="ui-dialog-scrim">
    <div aria-hidden="true" className="ui-dialog-backdrop" />
    <section
      aria-busy={busy || undefined}
      aria-describedby={descriptionId}
      aria-labelledby={titleId}
      aria-modal="true"
      className="ui-confirmation-dialog"
      onKeyDown={handleKeyDown}
      ref={dialogRef}
      role="alertdialog"
      tabIndex={-1}
    >
      <h2 className="ui-confirmation-dialog__title" id={titleId}>{title}</h2>
      <p className="ui-confirmation-dialog__description" id={descriptionId}>{description}</p>
      <div className="ui-confirmation-dialog__actions">
        <Button disabled={busy} onClick={onCancel} ref={cancelRef} variant="secondary">{cancelLabel}</Button>
        <Button disabled={busy} loading={busy} onClick={onConfirm} ref={confirmRef} variant={confirmVariant}>{confirmLabel}</Button>
      </div>
    </section>
  </div>;
}