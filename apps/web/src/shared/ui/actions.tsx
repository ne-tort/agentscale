import { forwardRef, type ButtonHTMLAttributes, type ReactNode } from "react";

export type ButtonVariant = "primary" | "secondary" | "quiet" | "danger";
export type ButtonSize = "small" | "medium";

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  readonly variant?: ButtonVariant;
  readonly size?: ButtonSize;
  readonly loading?: boolean;
  readonly className?: string;
}

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(function Button({
  children,
  className,
  disabled,
  loading = false,
  size = "medium",
  type = "button",
  variant = "secondary",
  ...props
}: ButtonProps, ref) {
  const classes = ["ui-button", `ui-button--${variant}`, `ui-button--${size}`, className]
    .filter(Boolean)
    .join(" ");

  return (
    <button
      {...props}
      aria-busy={loading || undefined}
      className={classes}
      disabled={disabled || loading}
      ref={ref}
      type={type}
    >
      {loading && <span aria-hidden="true" className="ui-spinner" />}
      {children}
    </button>
  );
});

export interface IconButtonProps extends Omit<ButtonHTMLAttributes<HTMLButtonElement>, "children"> {
  readonly label: string;
  readonly icon: ReactNode;
  readonly variant?: "quiet" | "danger";
  readonly className?: string;
}

export function IconButton({
  className,
  icon,
  label,
  type = "button",
  variant = "quiet",
  ...props
}: IconButtonProps) {
  const classes = ["ui-icon-button", `ui-icon-button--${variant}`, className]
    .filter(Boolean)
    .join(" ");

  return (
    <button {...props} aria-label={label} className={classes} title={props.title ?? label} type={type}>
      <span aria-hidden="true" className="ui-icon-button__icon">{icon}</span>
    </button>
  );
}