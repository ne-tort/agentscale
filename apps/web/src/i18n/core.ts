import type { Locale } from "../preferences/settings";

/**
 * One area's copy in both locales.
 *
 * Areas live in `./units/<namespace>.ts` and are picked up automatically, so two
 * people can add copy for different parts of the app without touching the same
 * file — the catalogue has no central list to conflict over.
 */
export type Unit = { ru: Record<string, string>; en: Record<string, string> };

/**
 * Declare an area's copy.
 *
 * Both locales must carry the same keys: a translated screen that silently falls
 * back to a key name is worse than a build error, and the check below runs at
 * module load, which means every test and every dev-server start hits it.
 */
export function defineStrings(unit: Unit): Unit {
  const ru = Object.keys(unit.ru).sort();
  const en = Object.keys(unit.en).sort();
  if (ru.length !== en.length || ru.some((key, index) => key !== en[index])) {
    const missingEn = ru.filter((key) => !(key in unit.en));
    const missingRu = en.filter((key) => !(key in unit.ru));
    throw new Error(
      `i18n: locale keys diverge (missing en: ${missingEn.join(", ") || "none"}; missing ru: ${missingRu.join(", ") || "none"})`,
    );
  }
  return unit;
}

export type Vars = Record<string, string | number>;

/** Substitutes `{name}` placeholders. Unknown placeholders are left as written. */
export function format(template: string, vars?: Vars): string {
  if (!vars) return template;
  return template.replace(/\{(\w+)\}/g, (match, name: string) =>
    name in vars ? String(vars[name]) : match,
  );
}

/**
 * Fallback: the key, minus its namespace.
 *
 * Showing `companies.new` on screen would be a bug report; showing "New" in the
 * wrong language is a design flaw the parity check already catches at build
 * time. This only fires if a component asks for a key nobody declared.
 *
 * Only the first word is capitalised: `newCompany` becomes "New company", not
 * "New Company", because these stand in for prose, not headings.
 */
export function fallbackFor(key: string): string {
  const bare = key.slice(key.indexOf(".") + 1);
  const tail = bare.slice(bare.lastIndexOf(".") + 1);
  const words = tail
    .replace(/([a-z0-9])([A-Z])/g, "$1 $2")
    .replace(/[_-]+/g, " ")
    .toLowerCase()
    .trim();
  return words.charAt(0).toUpperCase() + words.slice(1);
}

/**
 * Which plural form a count takes: Russian needs three ("1 компания",
 * "3 компании", "12 компаний") and English two.
 *
 * Units that vary by count declare `<key>_one`, `<key>_few`, `<key>_many`; this
 * says which suffix to ask the translator for. English maps `few` onto `other`,
 * so a unit can reuse the `_few` string there rather than declaring it twice.
 */
export function pluralForm(locale: Locale, count: number): "one" | "few" | "many" {
  const category = new Intl.PluralRules(locale).select(count);
  // The type is the union of what English uses: `other` covers everything
  // English does not treat as singular, which is exactly the `few` slot units
  // declare for Russian.
  if (category === "one") return "one";
  if (category === "few" || category === "other") return "few";
  return "many";
}

export type { Locale };
