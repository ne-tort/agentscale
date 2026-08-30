# Tabs — navigation shell

Tabs = **то, что видит пользователь** в панели навигации кабинета.

## TabDefinition

```json
{
  "id": "tab_suppliers",
  "title": "Поставщики",
  "order": 100,
  "icon": "local_shipping_outlined",
  "view_slug": "suppliers_list",
  "table_slug": "suppliers",
  "enabled": true,
  "system": false,
  "scope": {
    "projects": "all",
    "requires_assignment": true
  },
  "visibility": "visible",
  "badge": {
    "kind": "count",
    "table_slug": "suppliers",
    "filter": { "status": "pending" }
  }
}
```

| Field | Description |
|-------|-------------|
| `id` | Stable uuid or slug |
| `title` | **Nav label** — 1–3 слова (кнопка в tab bar / rail) |
| `order` | Sort key (system tabs: 0–99, dynamic: ≥100) |
| `icon` | Material icon (optional) |
| `view_slug` | ViewDefinition to render |
| `table_slug` | Shortcut for tables browser / MCP context |
| `enabled` | `false` → `visibility=disabled` |
| `system` | `true` only for platform-owned tabs (modules usually `false`) |
| `scope` | See [scope-bindings](07-scope-bindings.md) |
| `visibility` | `visible` \| `hidden` \| `disabled` |
| `badge` | Optional count dot |
| `nav` | Optional product-shell injection — see below |

## Shell navigation (Admin / Company / Employee)

Tabs may declare product-shell placement for **preview** and runtime when module is bound/deployed:

```json
{
  "id": "tab_suppliers",
  "title": "Поставщики",
  "order": 150,
  "icon": "local_shipping_outlined",
  "view_slug": "suppliers_list",
  "enabled": true,
  "nav": { "contour": "employee", "placement": "rail" }
}
```

| `nav.contour` | Meaning |
|---------------|---------|
| `admin` | Platform Admin shell rail / mobile **Management** hub |
| `company` | Company admin shell — same rules |
| `employee` \| `cabinet` | Employee cabinet shell — see placement below |

| `nav.placement` | Employee cabinet | Admin / Company |
|-----------------|------------------|-----------------|
| `rail` | Primary sidebar (wide) | Desktop rail |
| `management` | **Управление** hub only (narrow) | Mobile Management hub |
| `none` | Not in shell; in-page hub / deep link | Same |

**Defaults (employee / cabinet contour):**

- Tab **without** `nav` → `placement: management` (not rail).
- Explicit `placement: rail` — modules that belong in the sidebar (e.g. Suppliers).
- `nav.contour: admin|company` — unchanged; placement defaults to rail on desktop / management on narrow (existing Admin/Company behavior).

Without `nav` on admin/company tabs — tab stays in cabinet/preview TabBar only (default).

**Preview (seed editor):** `ModuleMetaPreviewPage` renders shell nav chips/rail mock for the **current module only** — not live Admin catalog merge.

**Live Admin/Company shell:** runtime merge from module catalog via [ShellNavLoader](../../../apps/flutter/lib/features/meta/shell_nav_loader.dart) — same rules as cabinet shell.

**Merge rules (when runtime applies):**

1. Load modules in scope for contour.
2. Read `tabs` slug; keep `enabled` tabs where `nav.contour` matches.
3. Sort by `order`, then `title`; title collision → «{title} · {moduleName}».
4. Append after platform-fixed nav items (Overview, Companies, …).

Desktop: rail destinations. Mobile: items in **Управление** hub — not bottom bar.

## Shell composition

```text
CabinetShell
├── system tabs (platform, always)
│     Projects | Chat | Context | Tables | Tools
└── dynamic tabs (from bound modules, merged + sorted by order)
      Module A: Поставщики
      Module B: Характеристики
```

**Merge rules:**

1. Load all modules bound to cabinet (`module_cabinet_bindings`).
2. For each module: read `tabs` slug; filter `enabled` + scope.
3. If module has `module_project_bindings` and user in project context — filter `scope.projects=bound`.
4. Sort by `order`, then `title`.
5. Collision on `title` — prefix with module name: «Поставщики · Ops».

## Nav presentation by breakpoint

| Breakpoint | Pattern |
|------------|---------|
| Narrow | Bottom tab bar (max 5 visible + «Ещё» hub) |
| Medium+ | Scrollable tab row under AppBar or secondary rail |

Meta задаёт **title + icon + order**; chrome — [07-ui-mobile-core](../../07-ui-mobile-core/responsive.md).

## Disabled vs hidden

| visibility | Nav | Direct URL |
|------------|-----|------------|
| `visible` | Normal | OK |
| `disabled` | Grayed, no tap | 403 |
| `hidden` | Not in nav | 404 (unless admin) |

Use `enabled: false` as alias for `visibility: disabled`.

## System tab reference (platform)

Not stored in module meta — hardcoded in shell:

| view_slug | title (ru) | order |
|-----------|------------|-------|
| `projects` | Проекты | 10 |
| `chat` | Чат | 20 |
| `context` | Контекст | 30 |
| `tables` | Таблицы | 40 |
| `tools` | Инструменты | 50 |

Module tabs **must not** reuse system `view_slug` values.

## Module without tabs

If module has tables but no `tabs` slug — auto-generate default tab:

```json
{
  "id": "auto_{module_id}_{table_slug}",
  "title": "{table.label}",
  "order": 500,
  "view_slug": "{table_slug}_list",
  "system": false
}
```

Only if corresponding default view exists or auto-view created.

## Project context

When employee inside `ProjectWorkspace`:

- Tabs with `scope.projects=bound` visible only if MP binding exists.
- Tabs with `scope.projects=all` always visible.
- Tab `projects` system — always leads back to project list.

Дальше: [actions-runtime](05-actions-runtime.md)
