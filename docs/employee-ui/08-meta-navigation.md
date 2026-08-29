# 08 — Meta navigation

## CabinetNavLoader

```dart
Future<({List<CabinetNavEntry> rail, List<CabinetNavEntry> management})>
    loadCabinetNavBundle(String cabinetId)

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

| `nav.placement` | Wide sidebar | Narrow «Управление» |
|---------------|--------------|---------------------|
| `rail` | yes | no |
| `management` | no | yes |
| `none` | no | no |

## System tab

- **Projects** — hardcoded in shell rail (order 10), **not** inside «Управление»

## Body

- `CabinetModuleHost` → `ViewInterpreterHost` + `CabinetDataController`
- TabBar or rail entry switches `view_slug`

## In-page injection

- `ui_json.kind: hub` / `profile_hub` → buttons inside the view
- Tab with `placement: none` + view hub — module not in shell nav, links on its page

## Not in v1

- `invoke_action` toolbar (stub)
- `board` view kind
- `nav.slot: "page.{slug}"` for named shell page injection (phase 2)
