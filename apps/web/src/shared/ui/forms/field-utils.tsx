import { useId, type ReactNode } from "react";

export type AriaInvalid = "true" | "false" | "grammar" | "spelling" | boolean | undefined;

export interface FieldPresentationProps {
  readonly id?: string;
  readonly label: string;
  readonly hint?: ReactNode;
  readonly error?: ReactNode;
  readonly className?: string;
  readonly "aria-describedby"?: string;
  readonly "aria-invalid"?: AriaInvalid;
}

export interface FieldIds {
  readonly fieldId: string;
  readonly hintId?: string;
  readonly errorId?: string;
  readonly describedBy?: string;
}

export function hasContent(value: ReactNode): boolean {
  return value !== undefined && value !== null && value !== false;
}

export function useFieldIds({ id, hint, error, "aria-describedby": existingDescription }: FieldPresentationProps): FieldIds {
  const generatedId = useId();
  const fieldId = id ?? generatedId;
  const hintId = hasContent(hint) ? `${fieldId}-hint` : undefined;
  const errorId = hasContent(error) ? `${fieldId}-error` : undefined;
  const describedBy = [existingDescription, hintId, errorId].filter(Boolean).join(" ") || undefined;
  return { fieldId, hintId, errorId, describedBy };
}

export function FieldMessages({ hint, error, hintId, errorId }: {
  readonly hint?: ReactNode;
  readonly error?: ReactNode;
  readonly hintId?: string;
  readonly errorId?: string;
}) {
  return <>
    {hasContent(hint) && <span className="form-field__hint" id={hintId}>{hint}</span>}
    {hasContent(error) && <span className="form-field__error" id={errorId} role="alert">{error}</span>}
  </>;
}