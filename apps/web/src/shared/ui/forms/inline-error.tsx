import type { ReactNode } from "react";

export interface InlineErrorProps {
  readonly message: ReactNode;
  readonly title?: string;
  readonly action?: ReactNode;
  readonly className?: string;
}

export function InlineError({ message, title, action, className }: InlineErrorProps) {
  return <div aria-atomic="true" className={["form-inline-error", className].filter(Boolean).join(" ")} role="alert">
    <div className="form-inline-error__copy">
      {title && <strong>{title}</strong>}
      <span>{message}</span>
    </div>
    {action && <div className="form-inline-error__action">{action}</div>}
  </div>;
}