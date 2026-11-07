import { createContext, useContext, useEffect, useMemo, useState, type PropsWithChildren } from "react";
import {
  DEFAULT_LOCALE,
  DEFAULT_THEME,
  LOCALE_STORAGE_KEY,
  THEME_STORAGE_KEY,
  parseLocale,
  parseTheme,
  readPreference,
  resolveTheme,
  writePreference,
  type Locale,
  type ResolvedTheme,
  type ThemeMode,
} from "./settings";

type SettingsValue = {
  locale: Locale;
  theme: ThemeMode;
  resolvedTheme: ResolvedTheme;
  setLocale: (locale: Locale) => void;
  setTheme: (theme: ThemeMode) => void;
};

const SettingsContext = createContext<SettingsValue | undefined>(undefined);

function prefersDark(): boolean | undefined {
  // `matchMedia` is missing in jsdom without a stub; `undefined` means "unknown"
  // and resolves to light rather than throwing during a test.
  return window.matchMedia?.("(prefers-color-scheme: dark)").matches;
}

export function SettingsProvider({ children }: PropsWithChildren) {
  const [locale, setLocaleState] = useState<Locale>(
    () => parseLocale(readPreference(LOCALE_STORAGE_KEY)) ?? DEFAULT_LOCALE,
  );
  const [theme, setThemeState] = useState<ThemeMode>(
    () => parseTheme(readPreference(THEME_STORAGE_KEY)) ?? DEFAULT_THEME,
  );
  const [systemDark, setSystemDark] = useState<boolean | undefined>(prefersDark);

  // Only follow the OS while the user has not chosen explicitly: 'system' has to
  // react to the OS switching appearance at sunset, but a manual choice must not
  // be overridden by it.
  useEffect(() => {
    if (theme !== "system") return;
    const query = window.matchMedia?.("(prefers-color-scheme: dark)");
    if (!query) return;
    const apply = () => setSystemDark(query.matches);
    apply();
    query.addEventListener("change", apply);
    return () => query.removeEventListener("change", apply);
  }, [theme]);

  const resolvedTheme = resolveTheme(theme, systemDark);

  // The theme lives on <html> so the page background, native form controls
  // (`color-scheme`) and anything rendered later than React all agree.
  useEffect(() => {
    document.documentElement.dataset.theme = resolvedTheme;
  }, [resolvedTheme]);

  // `lang` is what tells a screen reader which language to read, and the browser
  // which dictionary to use for hyphenation. Setting the theme without it left
  // a Russian page announcing itself as English.
  useEffect(() => {
    document.documentElement.lang = locale;
  }, [locale]);

  const value = useMemo<SettingsValue>(() => ({
    locale,
    theme,
    resolvedTheme,
    setLocale: (next) => {
      setLocaleState(next);
      writePreference(LOCALE_STORAGE_KEY, next);
    },
    setTheme: (next) => {
      setThemeState(next);
      writePreference(THEME_STORAGE_KEY, next);
    },
  }), [locale, theme, resolvedTheme]);

  return <SettingsContext.Provider value={value}>{children}</SettingsContext.Provider>;
}

export function useSettings(): SettingsValue {
  const value = useContext(SettingsContext);
  if (!value) throw new Error("useSettings must be used inside SettingsProvider");
  return value;
}
