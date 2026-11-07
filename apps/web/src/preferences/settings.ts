/**
 * UI language. Russian is the product default (the base audience and the
 * existing domain strings in this app are Russian); English stays available
 * because the code, API errors and docs are in English.
 */
export type Locale = "ru" | "en";

/**
 * `system` follows the OS setting and is the default. It is resolved to
 * `light`/`dark` at render time, so a user who never opens the settings menu
 * still gets their OS appearance.
 */
export type ThemeMode = "system" | "light" | "dark";

export type ResolvedTheme = "light" | "dark";

export const DEFAULT_LOCALE: Locale = "ru";
export const DEFAULT_THEME: ThemeMode = "system";

export const LOCALE_STORAGE_KEY = "agentscale.locale";
export const THEME_STORAGE_KEY = "agentscale.theme";

const LOCALES: readonly Locale[] = ["ru", "en"];
const THEMES: readonly ThemeMode[] = ["system", "light", "dark"];

/**
 * `localStorage` throws in privacy modes and is absent in some test hosts, and
 * a preference is never worth crashing the page over.
 */
export function readPreference(key: string): string | undefined {
  try {
    return window.localStorage.getItem(key) ?? undefined;
  } catch {
    return undefined;
  }
}

export function writePreference(key: string, value: string): void {
  try {
    window.localStorage.setItem(key, value);
  } catch {
    // Preference lost on reload; the session keeps working either way.
  }
}

export function parseLocale(raw: string | undefined): Locale | undefined {
  return LOCALES.find((locale) => locale === raw);
}

export function parseTheme(raw: string | undefined): ThemeMode | undefined {
  return THEMES.find((theme) => theme === raw);
}

/** `system` collapses to whichever the OS currently prefers. */
export function resolveTheme(mode: ThemeMode, prefersDark: boolean | undefined): ResolvedTheme {
  if (mode === "dark") return "dark";
  if (mode === "light") return "light";
  return prefersDark ? "dark" : "light";
}
