import {
  type ChangeEventHandler,
  type InputHTMLAttributes,
  type SelectHTMLAttributes,
  type TextareaHTMLAttributes,
} from "react";
import { FieldMessages, hasContent, type FieldPresentationProps, useFieldIds } from "./field-utils";

export type TextFieldProps = Omit<
  InputHTMLAttributes<HTMLInputElement>,
  "id" | "className" | "aria-describedby" | "aria-invalid"
> & FieldPresentationProps;

export function TextField({
  className, error, hint, id, label,
  "aria-describedby": existingDescription,
  "aria-invalid": ariaInvalid,
  type = "text",
  ...inputProps
}: TextFieldProps) {
  const ids = useFieldIds({ id, hint, error, "aria-describedby": existingDescription });
  return <div className={["form-field", className].filter(Boolean).join(" ")}>
    <label className="form-field__label" htmlFor={ids.fieldId}>{label}</label>
    <input
      {...inputProps}
      aria-describedby={ids.describedBy}
      aria-invalid={hasContent(error) ? true : ariaInvalid}
      className="form-control"
      id={ids.fieldId}
      type={type}
    />
    <FieldMessages error={error} errorId={ids.errorId} hint={hint} hintId={ids.hintId} />
  </div>;
}

export interface SelectOption<TValue extends string = string> {
  readonly value: TValue;
  readonly label: string;
  readonly disabled?: boolean;
}

export type SelectFieldProps<TValue extends string = string> = Omit<
  SelectHTMLAttributes<HTMLSelectElement>,
  "children" | "id" | "className" | "value" | "defaultValue" | "onChange" | "aria-describedby" | "aria-invalid"
> & FieldPresentationProps & {
  readonly options: ReadonlyArray<SelectOption<TValue>>;
  readonly value?: TValue | "" | null;
  readonly defaultValue?: TValue | "";
  readonly onValueChange?: (value: TValue | undefined) => void;
  readonly onChange?: ChangeEventHandler<HTMLSelectElement>;
  readonly placeholder?: string;
};

export function SelectField<TValue extends string = string>({
  className, error, hint, id, label, options, placeholder, required, value, defaultValue, onValueChange,
  "aria-describedby": existingDescription,
  "aria-invalid": ariaInvalid,
  ...selectProps
}: SelectFieldProps<TValue>) {
  const ids = useFieldIds({ id, hint, error, "aria-describedby": existingDescription });
  return <div className={["form-field", className].filter(Boolean).join(" ")}>
    <label className="form-field__label" htmlFor={ids.fieldId}>{label}</label>
    <select
      {...selectProps}
      aria-describedby={ids.describedBy}
      aria-invalid={hasContent(error) ? true : ariaInvalid}
      className="form-control"
      id={ids.fieldId}
      onChange={(event) => {
        selectProps.onChange?.(event);
        if (!event.defaultPrevented) onValueChange?.((event.currentTarget.value || undefined) as TValue | undefined);
      }}
      required={required}
      value={value === undefined || value === null ? undefined : String(value)}
      defaultValue={value === undefined ? (defaultValue as string | undefined) : undefined}
    >
      {placeholder && <option value="">{placeholder}</option>}
      {options.map((option) => <option disabled={option.disabled} key={option.value} value={option.value}>{option.label}</option>)}
    </select>
    <FieldMessages error={error} errorId={ids.errorId} hint={hint} hintId={ids.hintId} />
  </div>;
}

export type TextAreaFieldProps = Omit<
  TextareaHTMLAttributes<HTMLTextAreaElement>,
  "id" | "className" | "aria-describedby" | "aria-invalid"
> & FieldPresentationProps;

export function TextAreaField({
  className, error, hint, id, label,
  "aria-describedby": existingDescription,
  "aria-invalid": ariaInvalid,
  ...textareaProps
}: TextAreaFieldProps) {
  const ids = useFieldIds({ id, hint, error, "aria-describedby": existingDescription });
  return <div className={["form-field", className].filter(Boolean).join(" ")}>
    <label className="form-field__label" htmlFor={ids.fieldId}>{label}</label>
    <textarea
      {...textareaProps}
      aria-describedby={ids.describedBy}
      aria-invalid={hasContent(error) ? true : ariaInvalid}
      className="form-control form-control--textarea"
      id={ids.fieldId}
    />
    <FieldMessages error={error} errorId={ids.errorId} hint={hint} hintId={ids.hintId} />
  </div>;
}

export type CheckboxFieldProps = Omit<
  InputHTMLAttributes<HTMLInputElement>,
  "type" | "id" | "className" | "aria-describedby" | "aria-invalid"
> & FieldPresentationProps;

export function CheckboxField({
  className, error, hint, id, label,
  "aria-describedby": existingDescription,
  "aria-invalid": ariaInvalid,
  ...inputProps
}: CheckboxFieldProps) {
  const ids = useFieldIds({ id, hint, error, "aria-describedby": existingDescription });
  return <div className={["form-field form-field--checkbox", className].filter(Boolean).join(" ")}>
    <label className="form-checkbox__label" htmlFor={ids.fieldId}>
      <input
        {...inputProps}
        aria-describedby={ids.describedBy}
        aria-invalid={hasContent(error) ? true : ariaInvalid}
        className="form-checkbox"
        id={ids.fieldId}
        type="checkbox"
      />
      <span>{label}</span>
    </label>
    <FieldMessages error={error} errorId={ids.errorId} hint={hint} hintId={ids.hintId} />
  </div>;
}