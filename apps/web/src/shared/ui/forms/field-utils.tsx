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

/**
 * Derive the ids a field needs for its label, hint and error.
 *
 * Takes only the parts of ``FieldPresentationProps`` it actually reads: the hook is
 * called from field components that destructure ``label`` into their own markup, so
 * requiring the full props type (where ``label`` is mandatory) made every call site
 * a type error. ``FieldPresentationProps`` stays the contract for the components.
 */
export type FieldIdInputs = Pick<FieldPresentationProps, "id" | "hint" | "error" | "aria-describedby">;

export function useFieldIds({ id, hint, error, "aria-describedby": existingDescription }: FieldIdInputs): FieldIds {
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