# Cabinets — frontend

## Структура пакета

```text
features/cabinets/<profile_id>/
  cabinet_ui_module.dart   # entry
  presentation/screens/…
  # использует только core widgets + свои domain widgets при необходимости
```

Запрещено копировать локальные «свои» list/selector — только [07-ui-mobile-core](../07-ui-mobile-core/).

## Host

`CabinetShell` (employee) загружает UI module по `active_cabinet.profile_id`.

Tabs / destinations приходят из **manifest** кабинета (не хардкод procurement tabs в shell).

## Default UIs

| profile_id | Entry screens |
|------------|---------------|
| `generic-assistant` | Chat, Prompts |
| `equipment-procurement` | Projects, Specs, Variants, KP, Equipment |
