# 08 — Meta navigation

## CabinetNavLoader

```dart
Future<List<CabinetNavEntry>> load({
  required String cabinetId,
  String? projectId,
})
```

1. `GET /cabinets/{id}/modules`
2. For each module: load `tabs` meta slug
3. Filter: `enabled`, scope (project context if needed)
4. Merge + sort by `order`
5. Title collision → `"${tab.title} · ${module.name}"`

## System tab

- **Projects** — hardcoded, order 10, always first after overview

## Body

- `CabinetModuleHost` → `ViewInterpreterHost` + `CabinetDataController`
- TabBar or rail entry switches `view_slug`

## Not in v1

- `invoke_action` toolbar (stub)
- `board` view kind
