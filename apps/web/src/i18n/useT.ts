import { useCallback, useMemo } from "react";
import { useSettings } from "../preferences/SettingsProvider";
import { CATALOGUE, createTranslator, type Translate } from "./catalogue";
import { pluralForm, type Locale, type Vars } from "./core";

export type Plural = (key: string, count: number, vars?: Vars) => string;

export type I18n = {
  locale: Locale;
  t: Translate;
  /** `plural("common.records", 3)` → "3 записи"; asks for `_one`/`_few`/`_many`. */
  plural: Plural;
};

/**
 * The translator for the active locale.
 *
 * Components call this instead of importing copy: that is what makes the
 * language switch apply to the screen the user is looking at, rather than only
 * to the next screen they open.
 */
export function useI18n(): I18n {
  const { locale } = useSettings();
  return useMemo(() => {
    const t = createTranslator(locale, CATALOGUE);
    const plural: Plural = (key, count, vars) =>
      t(`${key}_${pluralForm(locale, count)}`, { count, ...vars });
    return { locale, t, plural };
  }, [locale]);
}

/** Shorthand when a component only needs `t`. */
export function useT(): Translate {
  const { t } = useI18n();
  return useCallback<Translate>((key, vars) => t(key, vars), [t]);
}
