/**
 * Saved module fixtures.
 *
 * The platform ships one first-party «Управление» module (`mod_management`) that owns
 * the prompt, MCP and file tables together. The fixture below mirrors that merged meta:
 * every table and view slug the backend keeps is present, plus the `management_hub`
 * landing view that links the three areas. There is no separate prompts/files/MCP
 * module id any more, so no fixture names one.
 */
export interface ModuleSeedFixture {
  readonly module: { id: string; name: string };
  /** Bind kind the project reports for this module; drives the runtime's editability. */
  readonly bindKind: "local" | "global";
  /** Whether the project may edit this module's data. */
  readonly childMayEdit: boolean;
  readonly project: { id: string; cabinet_id: string };
  readonly sessionId: string;
  readonly documents: Readonly<Record<string, unknown>>;
  readonly modules: unknown;
  readonly page: unknown;
  readonly profilePage: unknown;
}

function document(slug: string, body: unknown) { return { slug, body }; }

export const managementModuleFixture: ModuleSeedFixture = {
  module: { id: "mod_management", name: "Управление" },
  bindKind: "local",
  childMayEdit: true,
  project: { id: "project-beta", cabinet_id: "cabinet-beta" },
  sessionId: "session-beta",
  modules: { items: [{ id: "mod_management", module_id: "mod_management", name: "Управление", instance_id: "instance-beta", bind_kind: "local", child_may_edit: true }] },
  documents: {
    tables: document("tables", [
      { slug: "prompt_profiles", label: { ru: "Профили", en: "Profiles" }, enabled: true, storage_kind: "json_document", scope: { projects: "all", chats: "all" } },
      { slug: "profile_settings", label: "Profile settings", enabled: true, storage_kind: "json_document", scope: { projects: "all", chats: "all" } },
      { slug: "prompt_paths", label: { ru: "Промпты", en: "Prompts" }, enabled: true, storage_kind: "json_document", scope: { projects: "all", chats: "all" } },
      { slug: "mcp_packages", label: "MCP", enabled: true, storage_kind: "json_document", scope: { projects: "all", chats: "all" } },
      { slug: "files", label: "Файлы", enabled: true, storage_kind: "json_document", scope: { projects: "all", chats: "all" } },
    ]),
    columns: document("columns", [
      { table_slug: "prompt_profiles", name: "name", label: { ru: "Имя", en: "Name" }, type: "text", required: true },
      { table_slug: "prompt_profiles", name: "is_default", label: "Default", type: "bool", default: true },
      { table_slug: "prompt_profiles", name: "project_ids", label: "Projects", type: "json", default: [] },
      { table_slug: "prompt_paths", name: "profile_id", label: "Profile", type: "text", required: true },
      { table_slug: "prompt_paths", name: "name", label: { ru: "Имя", en: "Name" }, type: "text", required: true },
      { table_slug: "prompt_paths", name: "path", label: "Path", type: "text", default: "" },
      { table_slug: "prompt_paths", name: "files_json", label: "Prompts", type: "json", default: [] },
      { table_slug: "profile_settings", name: "profile_id", label: "Profile", type: "text", required: true },
      { table_slug: "profile_settings", name: "active", label: "Active", type: "bool", default: false },
      { table_slug: "mcp_packages", name: "name", type: "text", required: true },
      { table_slug: "mcp_packages", name: "version", type: "text", required: true },
      { table_slug: "mcp_packages", name: "enabled", type: "bool" },
      { table_slug: "mcp_packages", name: "file_ref", type: "file_ref" },
      { table_slug: "mcp_packages", name: "project_ids", type: "json" },
      { table_slug: "files", name: "name", label: "Имя", type: "text", required: true },
      { table_slug: "files", name: "target_path", label: "Путь", type: "text" },
      { table_slug: "files", name: "file_ref", label: "Файл", type: "file_ref" },
      { table_slug: "files", name: "project_ids", label: "Projects", type: "json", default: [] },
    ]),
    views: document("views", [
      { slug: "management_hub", table_slug: "prompt_profiles", kind: "hub", ui_json: { version: 1, kind: "hub", title: "Управление", items: [
        { title: "Промпты", target: { kind: "view", view: "prompt_profiles_list" } },
        { title: "MCP", target: { kind: "view", view: "mcp_packages_list" } },
        { title: "Файлы", target: { kind: "view", view: "files_list" } },
      ] } },
      { slug: "prompt_profiles", table_slug: "prompt_profiles", kind: "collection", ui_json: { version: 1, kind: "collection", scaffold: { title: { ru: "Профили", en: "Profiles" } }, title_field: "name", columns: [{ field: "name", label: { ru: "Имя", en: "Name" } }], row_tap: { kind: "open_view", view: "prompt_profiles_form" } } },
      { slug: "prompt_profiles_list", table_slug: "prompt_profiles", kind: "collection", ui_json: { version: 1, kind: "collection", scaffold: { title: { ru: "Профили", en: "Profiles" } }, title_field: "name", columns: [{ field: "name", label: { ru: "Имя", en: "Name" } }, { field: "prompts_count", label: "Prompts", source: "aggregate.prompt_paths.files_json", format: "list_count", foreign_key: "profile_id", join_field: "row_id" }], row_tap: { kind: "open_view", view: "prompts_hub" }, inline_add: { field: "name", title: "Добавить профиль" } } },
      { slug: "prompt_profiles_form", table_slug: "prompt_profiles", kind: "form", ui_json: { version: 1, kind: "form", mode: "edit", title: "Профиль", fields: [{ column: "project_ids", widget: "project_multiselect" }, { column: "name", widget: "value" }] } },
      { slug: "prompts_hub", table_slug: "prompt_paths", kind: "collection", ui_json: { version: 1, kind: "collection", scaffold: { title_template: { ru: "Профиль {name}", en: "Profile {name}" } }, title_field: "name", subtitle_fields: ["path"], columns: [{ field: "name", label: { ru: "Имя", en: "Name" } }, { field: "files_json", label: "Prompts", format: "list_count" }], context_bind: { profile_id: "contextRowId" }, context_header: { table_slug: "prompt_profiles", fields: [{ column: "name", widget: "value" }, { column: "project_ids", widget: "project_multiselect" }] }, row_tap: { kind: "open_view", view: "prompt_path_settings" }, inline_add: { field: "name", title: "Добавить промпт" }, profile_table: "prompt_profiles", settings_table: "profile_settings" } },
      { slug: "prompt_paths", table_slug: "prompt_paths", kind: "collection", ui_json: { version: 1, kind: "collection", scaffold: { title: { ru: "Промпты", en: "Prompts" } }, title_field: "name", subtitle_fields: ["path"], columns: [{ field: "name", label: { ru: "Имя", en: "Name" } }, { field: "files_json", label: "Prompts", format: "list_count" }], row_tap: { kind: "open_view", view: "prompt_path_settings" } } },
      { slug: "prompt_path_settings", table_slug: "prompt_paths", kind: "detail", ui_json: { version: 1, kind: "detail", mode: "edit", title_template: { ru: "Промпт {name}", en: "Prompt {name}" }, fields: [{ column: "name", widget: "value" }, { column: "path", widget: "value" }] } },
      { slug: "profile_settings", table_slug: "profile_settings", kind: "collection", ui_json: { version: 1, kind: "collection", scaffold: { title: "Настройки профиля" }, title_field: "profile_id", columns: [{ field: "profile_id", label: "Profile" }, { field: "active", label: "Active" }] } },
      { slug: "mcp_packages", table_slug: "mcp_packages", kind: "collection", ui_json: { version: 1, kind: "collection", title: "MCP", columns: [{ field: "name" }, { field: "version" }], row_tap: { kind: "open_form", view: "mcp_packages_form" } } },
      { slug: "mcp_packages_list", table_slug: "mcp_packages", kind: "collection", ui_json: { version: 1, kind: "collection", title: "MCP", columns: [{ field: "name" }, { field: "version" }], row_tap: { kind: "open_form", view: "mcp_packages_form" }, inline_add: { field: "name", title: "Add MCP package" } } },
      { slug: "mcp_packages_form", table_slug: "mcp_packages", kind: "form", ui_json: { version: 1, kind: "form", title: "MCP package", fields: [{ column: "project_ids", widget: "project_multiselect" }, { column: "name" }, { column: "version" }, { column: "enabled", widget: "switch" }, { column: "file_ref", widget: "file_upload", accept: ".zip" }] } },
      { slug: "files", table_slug: "files", kind: "collection", ui_json: { version: 1, kind: "collection", scaffold: { title: "Файлы" }, title_field: "name", columns: [{ field: "name", label: "Имя" }, { field: "target_path", label: "Путь" }], row_tap: { kind: "open_form", view: "files_form" } } },
      { slug: "files_list", table_slug: "files", kind: "collection", ui_json: { version: 1, kind: "collection", scaffold: { title: "Файлы" }, title_field: "name", columns: [{ field: "name", label: "Имя" }, { field: "target_path", label: "Путь" }], row_tap: { kind: "open_form", view: "files_form" }, inline_add: { field: "name", title: "Добавить файл" } } },
      { slug: "files_form", table_slug: "files", kind: "form", ui_json: { version: 1, kind: "form", mode: "edit", title: "Файл", fields: [{ column: "project_ids", widget: "project_multiselect" }, { column: "name", widget: "value" }, { column: "target_path", widget: "value" }, { column: "file_ref", widget: "file_upload" }] } },
      { slug: "files_primary", table_slug: "files", kind: "collection", ui_json: { version: 1, kind: "collection", scaffold: { title: "Файлы" }, columns: [{ field: "name", label: "Имя" }, { field: "file_ref", label: "Файл" }], row_tap: { kind: "open_form", view: "files_form" }, primary_action: { kind: "create_row", view: "files_form", label: "Новый файл" } } },
    ]),
    tabs: document("tabs", [
      { id: "tab_management", title: "Управление", order: 5, view_slug: "management_hub", table_slug: "prompt_profiles", enabled: true, default_project_bind: "global", nav: { contour: "employee", placement: "management" } },
      { id: "tab_prompts", title: "Промпты", order: 10, view_slug: "prompt_profiles_list", table_slug: "prompt_profiles", enabled: true, default_project_bind: "global", nav: { contour: "employee", placement: "management" } },
      { id: "tab_mcp", title: "MCP", order: 20, view_slug: "mcp_packages_list", table_slug: "mcp_packages", enabled: true, default_project_bind: "global", nav: { contour: "employee", placement: "management" } },
      { id: "tab_files", title: "Файлы", order: 30, view_slug: "files_list", table_slug: "files", enabled: true, default_project_bind: "global", nav: { contour: "employee", placement: "management" } },
    ]),
    actions: document("actions", []),
  },
  page: { items: [{ row_id: "file_row", session_id: null, body: { name: "guide.pdf", target_path: "docs/guide.pdf", file_ref: { asset_id: "asset-1", version_id: "version-1", blob_version_id: "version-1", filename: "guide.pdf", size: 128 } } }], total: 1, page: 1, page_size: 20 },
  profilePage: { items: [{ row_id: "prompt_path_1", session_id: null, body: { profile_id: "profile_default", name: "Tone", path: "prompts/tone", files_json: [{ name: "tone.md" }, { name: "style.md" }] } }], total: 1, page: 1, page_size: 50 },
};

export const equipmentModuleFixture: ModuleSeedFixture = {
  module: { id: "mod_equipment", name: "Подбор техники" },
  bindKind: "global",
  childMayEdit: false,
  project: { id: "project-equipment", cabinet_id: "cabinet-equipment" },
  sessionId: "session-equipment",
  modules: { items: [{ id: "mod_equipment", module_id: "mod_equipment", name: "Подбор техники", instance_id: "instance-equipment", bind_kind: "global", child_may_edit: false }] },
  documents: {
    tables: document("tables", [
      { slug: "catalogs", enabled: true, storage_kind: "json_document", scope: { projects: "all", chats: "all" } },
      { slug: "request_lines", enabled: true, storage_kind: "json_document", scope: { projects: "all", chats: "current" } },
      { slug: "master_price", enabled: true, storage_kind: "opensearch_virtual", scope: { projects: "all", chats: "all" } },
    ]),
    columns: document("columns", [
      { table_slug: "catalogs", name: "name", label: "Название", type: "text", required: true },
      { table_slug: "request_lines", name: "title", label: "Название", type: "text", required: true },
      { table_slug: "request_lines", name: "qty", label: "Кол-во", type: "number" },
    ]),
    views: document("views", [
      { slug: "equipment_hub", table_slug: "catalogs", kind: "hub", ui_json: { version: 1, kind: "hub", items: [{ title: "Бюджетирование", target: { kind: "view", view: "request_lines_list" }, scope: { active_chat: "required" } }, { title: "Master price", target: { kind: "view", view: "master_price_list" } }] } },
      { slug: "request_lines_list", table_slug: "request_lines", kind: "collection", ui_json: { version: 1, kind: "collection", scaffold: { title: { ru: "Позиции заказчика", en: "Request lines" } }, title_field: "title", columns: [{ field: "title", label: { ru: "Название", en: "Title" }, max_lines: 3 }, { field: "qty", label: "Qty" }], inline_add: { field: "title", title: "Добавить позицию" }, row_actions: [{ action: "budget_export", label: "Скачать бюджет" }] } },
      { slug: "master_price_list", table_slug: "master_price", kind: "collection", ui_json: { version: 1, kind: "collection", scaffold: { title: { ru: "Мастер прайс", en: "Master price" } }, title_field: "title", columns: [{ field: "title", label: { ru: "Название", en: "Title" } }] } },
    ]),
    tabs: document("tabs", [{ id: "tab_equipment", title: "Подбор техники", view_slug: "equipment_hub", enabled: true, default_project_bind: "global", nav: { contour: "employee", placement: "data" } }]),
    actions: document("actions", [{ id: "budget_export", kind: "equipment.budget_export", label: { ru: "Скачать xlsx", en: "Download xlsx" }, enabled: true }]),
  },
  page: { items: [{ row_id: "line-1", session_id: "session-equipment", body: { title: "Display", qty: 2 } }], total: 1, page: 1, page_size: 20 },
  profilePage: { items: [], total: 0, page: 1, page_size: 50 },
};

export const invalidModuleFixture = {
  tables: document("tables", [{ slug: "records", enabled: true, scope: { chats: "all" } }]),
  columns: document("columns", [{ table_slug: "records", name: "name", type: "text" }]),
  views: document("views", [{ slug: "broken", table_slug: "records", kind: "privileged_html", ui_json: { version: 1, kind: "privileged_html", html: "<script>steal()</script>" } }]),
  tabs: document("tabs", [{ id: "tab_records", title: "Records", view_slug: "broken", enabled: true }]),
  actions: document("actions", []),
};
