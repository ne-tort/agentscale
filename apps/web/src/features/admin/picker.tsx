import { useMemo, useState } from "react";
import { useI18n } from "../../i18n/useT";
import type { AdminCompany } from "./service";

export type PickItem = {
  id: string;
  label: string;
  secondary?: string;
  /**
   * The relationship is decided elsewhere and this list cannot change it — e.g.
   * a module already granted to every company. Rendering it as a normal checked
   * box meant unchecking it wrote nothing and said nothing.
   */
  locked?: boolean;
  lockedNote?: string;
};

/**
 * Picking things by name.
 *
 * The screen this replaced asked for "Company IDs (comma separated)" — an
 * instruction to go and find identifiers somewhere else, then paste them back.
 * Nobody administering a platform should have to do that.
 */
export function Picker({
  items,
  selected,
  onChange,
  searchLabel,
  emptyLabel,
}: {
  items: PickItem[];
  selected: string[];
  onChange: (next: string[]) => void;
  searchLabel: string;
  emptyLabel: string;
}) {
  const { t } = useI18n();
  const [query, setQuery] = useState("");
  const chosen = new Set(selected);
  const visible = useMemo(() => {
    const needle = query.trim().toLowerCase();
    if (!needle) return items;
    return items.filter(
      (item) =>
        item.label.toLowerCase().includes(needle) ||
        (item.secondary ?? "").toLowerCase().includes(needle),
    );
  }, [items, query]);

  function toggle(id: string) {
    const next = new Set(chosen);
    if (next.has(id)) next.delete(id);
    else next.add(id);
    onChange([...next]);
  }

  return (
    <div className="admin-picker">
      <input
        aria-label={searchLabel}
        className="admin-search"
        onChange={(event) => setQuery(event.target.value)}
        placeholder={searchLabel}
        type="search"
        value={query}
      />
      <p className="admin-muted">{t("admin.pickerSelected", { count: chosen.size })}</p>
      {visible.length === 0 ? (
        <p className="admin-muted">{query ? t("common.noMatches") : emptyLabel}</p>
      ) : (
        <ul className="admin-picker__list">
          {visible.map((item) => (
            <li key={item.id}>
              <label className="admin-picker__row">
                <input
                  checked={chosen.has(item.id)}
                  disabled={item.locked}
                  onChange={() => toggle(item.id)}
                  type="checkbox"
                />
                <span className="admin-picker__name">{item.label}</span>
                {item.locked && item.lockedNote && (
                  <span className="admin-picker__login">{item.lockedNote}</span>
                )}
                {!item.locked && item.secondary && (
                  <span className="admin-picker__login">{item.secondary}</span>
                )}
              </label>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

export function CompanyPicker({
  companies,
  selected,
  onChange,
}: {
  companies: AdminCompany[];
  selected: string[];
  onChange: (next: string[]) => void;
}) {
  const { t } = useI18n();
  return (
    <Picker
      emptyLabel={t("admin.pickerEmpty")}
      items={companies.map((company) => ({
        id: company.id,
        label: company.name,
        secondary: company.login,
      }))}
      onChange={onChange}
      searchLabel={t("admin.pickerSearch")}
      selected={selected}
    />
  );
}
