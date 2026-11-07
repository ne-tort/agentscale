import { defineStrings } from "../core";

/**
 * One word for one idea.
 *
 * The same `bind_kind` used to read "Только этот проект" on the module workspace
 * and "локальный" on the resources screen — two vocabularies for a concept a
 * person meets on both. These keys are the single source; screens ask for them
 * instead of declaring their own.
 */
export default defineStrings({
  ru: {
    /** Where the module's data lives for this project. */
    bindingLocal: "Только этот проект",
    bindingGlobal: "Все проекты модуля",
    /** Shorter form, for a line that already names the module. */
    bindingLocalShort: "только здесь",
    bindingGlobalShort: "во всех проектах",
    canEdit: "можно менять",
    readOnly: "только просмотр",
  },
  en: {
    bindingLocal: "This project only",
    bindingGlobal: "Every project of the module",
    bindingLocalShort: "here only",
    bindingGlobalShort: "in every project",
    canEdit: "editable",
    readOnly: "read-only",
  },
});
