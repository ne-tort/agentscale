import type { Locale } from "../preferences/settings";

/**
 * Dates and numbers in the language the interface is in.
 *
 * `Intl` defaults to the *browser's* locale. That is a different setting from
 * the one a person picks in the app: someone running an English browser who
 * chose Russian read "12 Nov 2026, 14:05" in a Russian screen, and a Russian
 * browser that had not been switched to Russian showed Cyrillic months inside
 * English copy. Every formatter here takes the locale explicitly, so what is
 * rendered follows the interface and not the operating system.
 *
 * `Intl.DateTimeFormat` and `Intl.NumberFormat` are expensive to construct, so
 * the formatters are cached per locale and style.
 */

export type DateInput = string | number | Date | null | undefined;

type DateStyle = {
  dateStyle?: "full" | "long" | "medium" | "short";
  timeStyle?: "full" | "long" | "medium" | "short";
  /** Used by the clock-only formatter, which cannot be expressed with styles. */
  hour?: "2-digit";
  minute?: "2-digit";
  timeZone?: string;
};

const DATE_TIME: DateStyle = { dateStyle: "medium", timeStyle: "short" };
const DATE_ONLY: DateStyle = { dateStyle: "medium" };
const TIME_ONLY: DateStyle = { hour: "2-digit", minute: "2-digit" };

const dateFormatters = new Map<string, Intl.DateTimeFormat>();
const numberFormatters = new Map<string, Intl.NumberFormat>();

function dateFormatter(locale: Locale, style: DateStyle): Intl.DateTimeFormat {
  const key = `${locale}|${style.dateStyle ?? ""}|${style.timeStyle ?? ""}|${style.hour ?? ""}|${style.timeZone ?? ""}`;
  let formatter = dateFormatters.get(key);
  if (!formatter) {
    formatter = new Intl.DateTimeFormat(locale, style);
    dateFormatters.set(key, formatter);
  }
  return formatter;
}

function numberFormatter(locale: Locale, options?: Intl.NumberFormatOptions): Intl.NumberFormat {
  const key = `${locale}|${JSON.stringify(options ?? {})}`;
  let formatter = numberFormatters.get(key);
  if (!formatter) {
    formatter = new Intl.NumberFormat(locale, options);
    numberFormatters.set(key, formatter);
  }
  return formatter;
}

/** A usable Date, or `undefined` for a value that is absent or cannot be read. */
export function toDate(value: DateInput): Date | undefined {
  if (value === null || value === undefined || value === "") return undefined;
  const date = value instanceof Date ? value : new Date(value);
  return Number.isFinite(date.getTime()) ? date : undefined;
}

/**
 * "12 нояб. 2026 г., 14:05" / "12 Nov 2026, 14:05".
 *
 * Returns `""` for an absent or unreadable value: the caller knows whether the
 * place wants "—", "никогда" or nothing at all.
 */
export function formatDateTime(value: DateInput, locale: Locale, timeZone?: string): string {
  const date = toDate(value);
  if (!date) return "";
  return dateFormatter(locale, timeZone ? { ...DATE_TIME, timeZone } : DATE_TIME).format(date);
}

/** "12 нояб. 2026 г." / "12 Nov 2026" — a day without a clock time. */
export function formatDate(value: DateInput, locale: Locale): string {
  const date = toDate(value);
  if (!date) return "";
  return dateFormatter(locale, DATE_ONLY).format(date);
}

/** "14:05" — a clock time without a day. */
export function formatTime(value: DateInput, locale: Locale): string {
  const date = toDate(value);
  if (!date) return "";
  return dateFormatter(locale, TIME_ONLY).format(date);
}

/** "1 234,5" / "1,234.5" — grouped digits in the interface's conventions. */
export function formatNumber(
  value: number | null | undefined,
  locale: Locale,
  options?: Intl.NumberFormatOptions,
): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return "";
  return numberFormatter(locale, options).format(value);
}
