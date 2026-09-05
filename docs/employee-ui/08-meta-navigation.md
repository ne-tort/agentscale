# 08 — Meta navigation

## CabinetNavLoader

```dart
Future<({
  List<CabinetNavEntry> rail,
  List<CabinetNavEntry> management,
  List<CabinetNavEntry> data,
})> loadCabinetNavBundle(String cabinetId, {String? projectId})

Future<List<CabinetNavEntry>> loadCabinetNavEntries(
    String cabinetId, {
    required CabinetNavPlacement placement,
    String? projectId,
})
```

1. Without `projectId`: `GET /cabinets/{id}/modules` (cabinet instance / bindings) — used for **rail**
2. With `projectId`: `GET /projects/{id}/runtime-modules` (project leaf) — used for **Management/Data hubs**
3. For each module: load `tabs` meta slug (cabinet or project runtime API)
4. Filter: `enabled`, exclude `nav.contour: admin|company`
5. Split by `nav.placement` (default **`management`** when `nav` absent)
6. Merge + sort by `order`
7. Title collision → `"${tab.title} · ${module.name}"`

| `nav.placement` | Wide sidebar | Narrow |
|---------------|--------------|--------|
| `rail` | module tab in rail (cabinet instance) | no |
| `management` | inside **«Управление»** (hub hidden if empty) | same |
| `data` | inside **«Данные»** (hub hidden if empty) | same |
| `none` | no | no |

Optional tab `subtitle` — shown under the title on Management/Data hub preference tiles.

## System pages (employee sidebar)

- **Projects** — hardcoded rail entry, **not** inside hubs
- **Управление** — only if ≥1 tab with `placement: management` for the selected project
- **Данные** — only if ≥1 tab with `placement: data` for the selected project
- Without selected project hub lists are empty → hubs hidden; hub page still shows CTA if opened with null `projectId`
- Hub module lists reload when the selected project changes

## Body

- `CabinetModuleHost` → `ViewInterpreterHost` + `CabinetDataController`
- **Rail:** omit `projectId` → cabinet instance meta/data APIs
- **Hubs:** pass explicit `projectId` → `/projects/{id}/runtime-modules/*` (never infer from selection silently)
- Hub rows: `AppNavPreference` (same chrome as project settings)

## In-page injection

- `ui_json.kind: hub` / `profile_hub` → buttons inside the view
- Tab with `placement: none` + view hub — module not in shell nav, links on its page

## Not in v1

- `invoke_action` toolbar (stub)
- `board` view kind
- `nav.slot: "page.{slug}"` for named shell page injection (phase 2)
- Actions/secrets edit from hubs on project owner (API backlog)
