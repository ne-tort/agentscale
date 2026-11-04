import { useId, type ReactNode } from "react";
import { Button, IconButton } from "./actions";

export type StatusVariant = "neutral" | "info" | "success" | "warning" | "danger";

export interface StatusBadgeProps {
  readonly children: ReactNode;
  readonly variant?: StatusVariant;
}

export function StatusBadge({ children, variant = "neutral" }: StatusBadgeProps) {
  return <span className={`ui-status-badge ui-status-badge--${variant}`}>{children}</span>;
}

// ``InlineError`` lives in ``./forms/inline-error`` and is re-exported from the
// foundation barrel. It used to be defined here as well, with the same prop names but
// a different class vocabulary (``ui-inline-error`` vs ``form-inline-error``) and no
// ``title``, so two barrels exported the same component name with different rendering.
// The forms variant is the superset: keep one definition and re-export it below.
export { InlineError, type InlineErrorProps } from "./forms/inline-error";

export interface EmptyStateProps {
  readonly title: string;
  readonly description?: ReactNode;
  readonly action?: ReactNode;
  readonly className?: string;
}

export function EmptyState({ title, description, action, className }: EmptyStateProps) {
  const titleId = useId();
  return <section aria-labelledby={titleId} className={["ui-empty-state", className].filter(Boolean).join(" ")}>
    <h2 className="ui-empty-state__title" id={titleId}>{title}</h2>
    {description && <div className="ui-empty-state__description">{description}</div>}
    {action && <div className="ui-empty-state__action">{action}</div>}
  </section>;
}

export interface LoadingStateProps {
  readonly label?: string;
  readonly className?: string;
}

export function LoadingState({ label = "Loading", className }: LoadingStateProps) {
  return <div aria-live="polite" className={["ui-loading-state", className].filter(Boolean).join(" ")} role="status">
    <span aria-hidden="true" className="ui-spinner" />
    <span>{label}</span>
  </div>;
}

export type OperationVariant = "success" | "error" | "pending" | "info";

export interface OperationResultProps {
  readonly title: string;
  readonly message?: ReactNode;
  readonly variant: OperationVariant;
  readonly action?: ReactNode;
  readonly className?: string;
}

export function OperationResult({ title, message, variant, action, className }: OperationResultProps) {
  const isError = variant === "error";
  return <section
    aria-atomic="true"
    aria-live={isError ? "assertive" : "polite"}
    className={["ui-operation-result", `ui-operation-result--${variant}`, className].filter(Boolean).join(" ")}
    role={isError ? "alert" : "status"}
  >
    <div className="ui-operation-result__copy">
      <strong>{title}</strong>
      {message && <span>{message}</span>}
    </div>
    {action && <div className="ui-operation-result__action">{action}</div>}
  </section>;
}

export interface ToastProps {
  readonly title: string;
  readonly message?: ReactNode;
  readonly variant?: OperationVariant;
  readonly onDismiss?: () => void;
  readonly action?: ReactNode;
}

export function Toast({ title, message, variant = "info", onDismiss, action }: ToastProps) {
  const isError = variant === "error";
  return <article
    aria-atomic="true"
    aria-live={isError ? "assertive" : "polite"}
    className={`ui-toast ui-toast--${variant}`}
    role={isError ? "alert" : "status"}
  >
    <div className="ui-toast__copy">
      <strong>{title}</strong>
      {message && <span>{message}</span>}
      {action && <div className="ui-toast__action">{action}</div>}
    </div>
    {onDismiss && <IconButton icon="x" label={`Dismiss ${title}`} onClick={onDismiss} />}
  </article>;
}

export interface ToastRegionProps {
  readonly children: ReactNode;
  readonly label?: string;
}

export function ToastRegion({ children, label = "Notifications" }: ToastRegionProps) {
  return <div aria-label={label} className="ui-toast-region" role="region">{children}</div>;
}

export interface RetryActionProps {
  readonly onRetry: () => void;
  readonly label?: string;
}

export function RetryAction({ onRetry, label = "Retry" }: RetryActionProps) {
  return <Button onClick={onRetry} size="small" variant="secondary">{label}</Button>;
}