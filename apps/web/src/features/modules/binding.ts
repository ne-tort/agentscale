import type { Translate } from "../../i18n/catalogue";

/**
 * How a module is bound to a project, in words.
 *
 * `bind_kind` is `local` or `global` — the data model's vocabulary. Everything a
 * person reads goes through here, so the same concept cannot be called two
 * different things on two screens.
 */
export function bindingLabel(t: Translate, kind: string | undefined, short = false): string {
  if (kind === "global") return short ? t("binding.bindingGlobalShort") : t("binding.bindingGlobal");
  return short ? t("binding.bindingLocalShort") : t("binding.bindingLocal");
}

/** Whether the project may edit a module it was granted. */
export function editRightsLabel(t: Translate, childMayEdit: boolean | undefined): string {
  return childMayEdit ? t("binding.canEdit") : t("binding.readOnly");
}
