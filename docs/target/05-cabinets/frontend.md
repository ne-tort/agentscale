# Cabinets — frontend (dynamic shell)

## Канон

Один **Dynamic Cabinet Shell** на все кабинеты. Доменные feature-модули не добавляются.

```text
apps/flutter/lib/
  core/           # widgets, EntityCollection, theme
  shell/          # auth contours, host
  cabinets/
    dynamic_shell/    # tab host + meta interpreters ONLY
    base_system_tabs/ # optional thin wrappers for Projects/Chat/Context
```

Запрещено: `cabinets/equipment_procurement/…` как способ нового домена.

## Поведение

1. Load `CabinetInstance` + `meta.tabs`.  
2. Render system tabs + dynamic tabs.  
3. Dynamic tab → fetch view + rows → `AppEntityCollection` / form interpreter ([meta-and-ui](meta-and-ui.md)).  
4. Tables / Tools system tabs — CRUD meta via API (и те же контракты, что MCP).

## Переиспользование

Только `core` + session. Никакого доменного UI kit.
