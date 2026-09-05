# 08 — Meta navigation

## CabinetNavLoader

```dart
Future<({
  List<CabinetNavEntry> rail,
  List<CabinetNavEntry> management,
  List<CabinetNavEntry> data,
})> loadCabinetNavBundle(String cabinetId)

Future<List<CabinetNavEntry>> loadCabinetNavEntries(
    String cabinetId, {
    required CabinetNavPlacement placement,
})
```

1. `GET /cabinets/{id}/modules`
2. For each module: load `tabs` meta slug
3. Filter: `enabled`, exclude `nav.contour: admin|company`
4. Split by `nav.placement` (default **`management`** when `nav` absent)
5. Merge + sort by `order`
6. Title collision → `"${tab.title} · ${module.name}"`

| `nav.placement` | Wide sidebar | Narrow |
|---------------|--------------|--------|
| `rail` | module tab in rail | no |
| `management` | inside **«Управление»** (hub hidden if empty) | same |
| `data` | inside **«Данные»** (hub hidden if empty) | same |
| `none` | no | no |

Optional tab `subtitle` — shown under the title on Management/Data hub preference tiles.

## System pages (employee sidebar)

- **Projects** — hardcoded rail entry, **not** inside hubs
- **Управление** — only if ≥1 tab with `placement: management`
- **Данные** — only if ≥1 tab with `placement: data` (meta tables / meta syntax)

## Body

- `CabinetModuleHost` → `ViewInterpreterHost` + `CabinetDataController`
- Hub rows: `AppNavPreference` (same chrome as project settings)

## In-page injection

- `ui_json.kind: hub` / `profile_hub` → buttons inside the view
- Tab with `placement: none` + view hub — module not in shell nav, links on its page

## Not in v1

- `invoke_action` toolbar (stub)
- `board` view kind
- `nav.slot: "page.{slug}"` for named shell page injection (phase 2)
