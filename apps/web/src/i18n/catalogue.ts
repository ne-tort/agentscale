import { fallbackFor, format, type Locale, type Unit, type Vars } from "./core";

/**
 * Every `units/*.ts` file, keyed by its file name, which becomes the namespace.
 *
 * `import.meta.glob` rather than an explicit list: adding copy for a new area
 * means adding one file, never editing a registry that everyone else is editing
 * too.
 */
const units = import.meta.glob("./units/*.ts", { eager: true }) as Record<
  string,
  { default: Unit }
>;

export type Catalogue = Record<Locale, Record<string, string>>;

function namespaceOf(path: string): string {
  return path.replace("./units/", "").replace(/\.ts$/, "");
}

export function buildCatalogue(source: Record<string, { default: Unit }> = units): Catalogue {
  const catalogue: Catalogue = { ru: {}, en: {} };
  for (const [path, module] of Object.entries(source)) {
    const namespace = namespaceOf(path);
    const unit = module.default;
    for (const locale of ["ru", "en"] as const) {
      for (const [key, value] of Object.entries(unit[locale])) {
        catalogue[locale][`${namespace}.${key}`] = value;
      }
    }
  }
  return catalogue;
}

export const CATALOGUE = buildCatalogue();

export type Translate = (key: string, vars?: Vars) => string;

export function createTranslator(locale: Locale, catalogue: Catalogue = CATALOGUE): Translate {
  return (key, vars) => {
    const template = catalogue[locale][key];
    if (template === undefined) return fallbackFor(key);
    return format(template, vars);
  };
}

/**
 * A key that exists in one locale and not the other is a broken screen, not a
 * broken build — so fail loudly at load instead of waiting for someone to notice
 * a lone English word.
 */
function assertCatalogueParity(catalogue: Catalogue): void {
  const missing = Object.keys(catalogue.ru).filter((key) => !(key in catalogue.en));
  const extra = Object.keys(catalogue.en).filter((key) => !(key in catalogue.ru));
  if (missing.length > 0 || extra.length > 0) {
    throw new Error(
      `i18n: catalogue keys diverge (en only: ${extra.join(", ") || "none"}; ru only: ${missing.join(", ") || "none"})`,
    );
  }
}

assertCatalogueParity(CATALOGUE);
