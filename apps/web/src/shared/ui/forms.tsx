import { useId, type InputHTMLAttributes, type ReactNode, type SelectHTMLAttributes, type TextareaHTMLAttributes } from "react";

type DescribedFieldProps = {
  readonly label: string;
  readonly hint?: ReactNode;
  readonly error?: string;
  readonly className?: string;
};

function describedBy(existing: string | undefined, hintId: string | undefined, errorId: string | undefined) {
  return [existing, hintId, errorId].filter(Boolean).join(" ") || undefined;
}

function FieldMessages({ hint, error, hintId, errorId }: {
  readonly hint?: ReactNode;
  readonly error?: string;
  readonly hintId?: string;
  readonly errorId?: string;
}) {
  return <>
    {hint && <span className="ui-field__hint" id={hintId}>{hint}</span>}
    {error && <span className="ui-field__error" id={errorId}>{error}</span>}
  </>;
}

export interface TextFieldProps extends InputHTMLAttributes<HTMLInputElement>, DescribedFieldProps {}

export function TextField({
  className,
  error,
  hint,
  id,
  label,
  "aria-describedby": existingDescription,
  "aria-invalid": ariaInvalid,
  ...inputProps
}: TextFieldProps) {
  const generatedId = useId();
  const fieldId = id ?? generatedId;
  const hintId = hint ? `${fieldId}-hint` : undefined;
  const errorId = error ? `${fieldId}-error` : undefined;
  const classes = ["ui-field", className].filter(Boolean).join(" ");

  return (
    <div className={classes}>
      <label className="ui-field__label" htmlFor={fieldId}>{label}</label>
      <input
        {...inputProps}
        aria-describedby={describedBy(existingDescription, hintId, errorId)}
        aria-invalid={error ? true : ariaInvalid}
        className="ui-control"
        id={fieldId}
      />
      <FieldMessages error={error} errorId={errorId} hint={hint} hintId={hintId} />
    </div>
  );
}

export interface SelectFieldProps extends Omit<SelectHTMLAttributes<HTMLSelectElement>, "children">, DescribedFieldProps {
  readonly options: ReadonlyArray<{ readonly value: string; readonly label: string; readonly disabled?: boolean }>;
  readonly placeholder?: string;
}

export function SelectField({
  className,
  error,
  hint,
  id,
  label,
  options,
  placeholder,
  "aria-describedby": existingDescription,
  "aria-invalid": ariaInvalid,
  ...selectProps
}: SelectFieldProps) {
  const generatedId = useId();
  const fieldId = id ?? generatedId;
  const hintId = hint ? `${fieldId}-hint` : undefined;
  const errorId = error ? `${fieldId}-error` : undefined;
  const classes = ["ui-field", className].filter(Boolean).join(" ");

  return (
    <div className={classes}>
      <label className="ui-field__label" htmlFor={fieldId}>{label}</label>
      <select
        {...selectProps}
        aria-describedby={describedBy(existingDescription, hintId, errorId)}
        aria-invalid={error ? true : ariaInvalid}
        className="ui-control"
        id={fieldId}
      >
        {placeholder && <option value="">{placeholder}</option>}
        {options.map((option) => <option disabled={option.disabled} key={option.value} value={option.value}>{option.label}</option>)}
      </select>
      <FieldMessages error={error} errorId={errorId} hint={hint} hintId={hintId} />
    </div>
  );
}

export interface TextAreaFieldProps extends TextareaHTMLAttributes<HTMLTextAreaElement>, DescribedFieldProps {}

export function TextAreaField({
  className,
  error,
  hint,
  id,
  label,
  "aria-describedby": existingDescription,
  "aria-invalid": ariaInvalid,
  ...textareaProps
}: TextAreaFieldProps) {
  const generatedId = useId();
  const fieldId = id ?? generatedId;
  const hintId = hint ? `${fieldId}-hint` : undefined;
  const errorId = error ? `${fieldId}-error` : undefined;
  const classes = ["ui-field", className].filter(Boolean).join(" ");

  return (
    <div className={classes}>
      <label className="ui-field__label" htmlFor={fieldId}>{label}</label>
      <textarea
        {...textareaProps}
        aria-describedby={describedBy(existingDescription, hintId, errorId)}
        aria-invalid={error ? true : ariaInvalid}
        className="ui-control ui-control--textarea"
        id={fieldId}
      />
      <FieldMessages error={error} errorId={errorId} hint={hint} hintId={hintId} />
    </div>
  );
}

export interface CheckboxFieldProps extends Omit<InputHTMLAttributes<HTMLInputElement>, "type"> {
  readonly label: string;
  readonly hint?: ReactNode;
  readonly className?: string;
}

export function CheckboxField({ className, hint, id, label, ...inputProps }: CheckboxFieldProps) {
  const generatedId = useId();
  const fieldId = id ?? generatedId;
  const hintId = hint ? `${fieldId}-hint` : undefined;
  const existingDescription = inputProps["aria-describedby"];

  return (
    <div className={["ui-checkbox-field", className].filter(Boolean).join(" ")}>
      <label className="ui-checkbox-field__label" htmlFor={fieldId}>
        <input
          {...inputProps}
          aria-describedby={describedBy(existingDescription, hintId, undefined)}
          className="ui-checkbox"
          id={fieldId}
          type="checkbox"
        />
        <span>{label}</span>
      </label>
      {hint && <span className="ui-field__hint" id={hintId}>{hint}</span>}
    </div>
  );
}