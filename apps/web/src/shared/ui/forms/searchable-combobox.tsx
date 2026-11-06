import {
  useEffect,
  useMemo,
  useRef,
  useState,
  type FocusEvent,
  type ChangeEventHandler,
  type FocusEventHandler,
  type InputHTMLAttributes,
  type KeyboardEvent,
  type KeyboardEventHandler,
} from "react";
import { FieldMessages, hasContent, type FieldPresentationProps, useFieldIds } from "./field-utils";

export interface SearchableOption<TValue extends string = string> {
  readonly value: TValue;
  readonly label: string;
  readonly disabled?: boolean;
  readonly keywords?: readonly string[];
}

export function filterComboboxOptions<TValue extends string>(
  options: ReadonlyArray<SearchableOption<TValue>>,
  query: string,
): SearchableOption<TValue>[] {
  const needle = query.trim().toLocaleLowerCase();
  if (!needle) return [...options];
  return options.filter((option) => [option.label, ...(option.keywords ?? [])]
    .some((text) => text.toLocaleLowerCase().includes(needle)));
}

export type SearchableComboboxProps<TValue extends string = string> = Omit<
  InputHTMLAttributes<HTMLInputElement>,
  "id" | "className" | "type" | "role" | "value" | "defaultValue" | "onChange" | "onFocus" | "onKeyDown" | "aria-describedby" | "aria-invalid"
> & FieldPresentationProps & {
  readonly options: ReadonlyArray<SearchableOption<TValue>>;
  readonly value?: TValue | null;
  readonly defaultValue?: TValue | null;
  readonly onValueChange?: (value: TValue | undefined) => void;
  readonly onQueryChange?: (query: string) => void;
  readonly onChange?: ChangeEventHandler<HTMLInputElement>;
  readonly onFocus?: FocusEventHandler<HTMLInputElement>;
  readonly onKeyDown?: KeyboardEventHandler<HTMLInputElement>;
  readonly noOptionsMessage?: string;
  readonly clearLabel?: string;
  readonly requiredMessage?: string;
};

export function SearchableCombobox<TValue extends string = string>({
  className, error, hint, id, label, name, form, options, value, defaultValue, onValueChange, onQueryChange,
  onChange, onFocus, onKeyDown,
  noOptionsMessage = "No matching options.", clearLabel = "Clear selection", required = false,
  requiredMessage = "Select an option from the list.", disabled, readOnly, placeholder,
  "aria-describedby": existingDescription,
  "aria-invalid": ariaInvalid,
  ...inputProps
}: SearchableComboboxProps<TValue>) {
  const ids = useFieldIds({ id, hint, error, "aria-describedby": existingDescription });
  const listboxId = `${ids.fieldId}-listbox`;
  const controlled = value !== undefined;
  const [internalValue, setInternalValue] = useState<TValue | undefined>(defaultValue ?? undefined);
  const selectedValue = controlled ? value ?? undefined : internalValue;
  const selectedOption = options.find((option) => option.value === selectedValue);
  const [internalSearchText, setInternalSearchText] = useState(selectedOption?.label ?? "");
  const [open, setOpen] = useState(false);
  const [internalSearching, setInternalSearching] = useState(false);
  const [activeIndex, setActiveIndex] = useState(-1);
  const wrapperRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const searchText = internalSearchText;
  const searching = internalSearching;
  const setSearchText = setInternalSearchText;
  const setSearching = setInternalSearching;

  useEffect(() => {
    if (!searching) setSearchText(selectedOption?.label ?? "");
  }, [searching, selectedOption?.label]);

  useEffect(() => {
    const hasSelection = selectedOption && !searching && searchText === selectedOption.label;
    inputRef.current?.setCustomValidity(required && !hasSelection ? requiredMessage : "");
  }, [required, requiredMessage, searchText, searching, selectedOption]);

  const filteredOptions = useMemo(
    () => filterComboboxOptions(options, open && searching ? searchText : ""),
    [open, options, searchText, searching],
  );
  const activeOption = activeIndex >= 0 ? filteredOptions[activeIndex] : undefined;

  const updateValue = (nextValue: TValue | undefined) => {
    if (!controlled) setInternalValue(nextValue);
    onValueChange?.(nextValue);
  };

  const closeAndRestore = () => {
    setOpen(false);
    setSearching(false);
    setActiveIndex(-1);
    setSearchText(selectedOption?.label ?? "");
    inputRef.current?.setCustomValidity(required && !selectedOption ? requiredMessage : "");
  };

  const chooseOption = (option: SearchableOption<TValue>) => {
    if (option.disabled || disabled || readOnly) return;
    updateValue(option.value);
    setSearchText(option.label);
    setSearching(false);
    setOpen(false);
    setActiveIndex(-1);
    inputRef.current?.setCustomValidity("");
  };

  const moveActive = (direction: 1 | -1) => {
    if (filteredOptions.length === 0) return;
    let index = activeIndex < 0 ? (direction === 1 ? -1 : 0) : activeIndex;
    for (let count = 0; count < filteredOptions.length; count += 1) {
      index = (index + direction + filteredOptions.length) % filteredOptions.length;
      if (!filteredOptions[index].disabled) {
        setActiveIndex(index);
        return;
      }
    }
    setActiveIndex(-1);
  };

  const handleKeyDown = (event: KeyboardEvent<HTMLInputElement>) => {
    onKeyDown?.(event);
    if (event.defaultPrevented) return;
    if (event.nativeEvent.isComposing) return;
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      if (readOnly) return;
      event.preventDefault();
      if (!open) {
        setOpen(true);
        setSearching(false);
        setActiveIndex(-1);
      }
      moveActive(event.key === "ArrowDown" ? 1 : -1);
      return;
    }
    if (!open) return;
    if (event.key === "Enter") {
      if (activeOption && !activeOption.disabled) {
        event.preventDefault();
        chooseOption(activeOption);
      } else event.preventDefault();
      return;
    }
    if (event.key === "Escape") {
      event.preventDefault();
      closeAndRestore();
    }
  };

  const handleBlur = (event: FocusEvent<HTMLDivElement>) => {
    if (event.relatedTarget instanceof Node && wrapperRef.current?.contains(event.relatedTarget)) return;
    closeAndRestore();
  };

  const handleInput = (nextQuery: string) => {
    setSearchText(nextQuery);
    setSearching(true);
    setOpen(true);
    setActiveIndex(-1);
    if (selectedValue !== undefined) updateValue(undefined);
    onQueryChange?.(nextQuery);
    inputRef.current?.setCustomValidity(required ? requiredMessage : "");
  };

  return <div className={["form-field form-combobox", className].filter(Boolean).join(" ")} onBlur={handleBlur} ref={wrapperRef}>
    <label className="form-field__label" htmlFor={ids.fieldId}>{label}</label>
    <div className="form-combobox__control">
      <input
        {...inputProps}
        aria-activedescendant={open && activeOption ? `${listboxId}-option-${activeIndex}` : undefined}
        aria-autocomplete="list"
        aria-controls={listboxId}
        aria-describedby={ids.describedBy}
        aria-expanded={open}
        aria-invalid={hasContent(error) ? true : ariaInvalid}
        aria-required={required || undefined}
        autoComplete="off"
        className="form-control form-combobox__input"
        disabled={disabled}
        form={form}
        id={ids.fieldId}
        onChange={(event) => {
          onChange?.(event);
          handleInput(event.currentTarget.value);
        }}
        onFocus={(event) => {
          onFocus?.(event);
          if (readOnly || event.defaultPrevented) return;
          setOpen(true);
          setSearching(false);
          setActiveIndex(-1);
        }}
        onKeyDown={handleKeyDown}
        placeholder={placeholder}
        readOnly={readOnly}
        ref={inputRef}
        required={required}
        role="combobox"
        type="text"
        value={searchText}
      />
      {selectedOption && !disabled && !readOnly && <button
        aria-label={clearLabel}
        className="form-combobox__clear"
        onMouseDown={(event) => event.preventDefault()}
        onClick={() => {
          updateValue(undefined);
          setSearchText("");
          setSearching(true);
          setOpen(true);
          setActiveIndex(-1);
          onQueryChange?.("");
          inputRef.current?.setCustomValidity(required ? requiredMessage : "");
          inputRef.current?.focus();
        }}
        type="button"
      >Clear</button>}
    </div>
    <ul aria-label={`${label} options`} className="form-combobox__listbox" hidden={!open} id={listboxId} role="listbox">
      {filteredOptions.length === 0
        ? <li className="form-combobox__empty" role="presentation">{noOptionsMessage}</li>
        : filteredOptions.map((option, index) => <li
          aria-disabled={option.disabled || undefined}
          aria-selected={option.value === selectedValue}
          className={[
            "form-combobox__option",
            option.value === selectedValue && "form-combobox__option--selected",
            index === activeIndex && "form-combobox__option--active",
            option.disabled && "form-combobox__option--disabled",
          ].filter(Boolean).join(" ")}
          id={`${listboxId}-option-${index}`}
          key={option.value}
          onClick={() => chooseOption(option)}
          onMouseDown={(event) => event.preventDefault()}
          role="option"
        >{option.label}</li>)}
    </ul>
    <FieldMessages error={error} errorId={ids.errorId} hint={hint} hintId={ids.hintId} />
    {name && <input
      aria-hidden="true"
      className="form-visually-hidden"
      disabled={disabled}
      form={form}
      name={name}
      tabIndex={-1}
      type="hidden"
      value={selectedOption && !searching && searchText === selectedOption.label ? selectedOption.value : ""}
    />}
  </div>;
}

export const SearchableSelect = SearchableCombobox;