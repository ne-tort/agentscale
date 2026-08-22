# Cabinets — frontend

## Файловая изоляция

```text
apps/flutter/lib/
  core/                         # theme, widgets, breakpoints — ОБЩЕЕ
  shell/                        # login contours, cabinet host, nav — ПЛАТФОРМА
  cabinets/
    generic_assistant/          # base cabinet UI (весь экранный домен)
      cabinet_ui_module.dart
      presentation/…
    equipment_procurement/      # copy+extend base
      cabinet_ui_module.dart
      presentation/…
```

Запрещено:

- Доменные экраны кабинета в `features/<domain>` вне `cabinets/<id>/`.
- Импорт `cabinets.equipment_…` из `cabinets.generic_…` кроме через явный shared kit (если появится `cabinets/_base`).
- Свои list/button/table в обход [07-ui-mobile-core](../07-ui-mobile-core/).

## Что переиспользовать

| Можно | Нельзя переносить в cabinet pack |
|-------|----------------------------------|
| `core/theme`, `core/widgets`, EntityCollection | Доменные procurement widgets → в core |
| Session / auth от shell (principal, tokens) | Свой параллельный login stack |
| Host slots: AppBar context, bottom nav from manifest | Hardcode tabs другого кабинета в shell |

## Host

`CabinetShell` загружает модуль:

```text
active_cabinet.profile_id → CabinetUiModule registry → buildTab / routes
```

Shell **не** знает labels tabs доменного кабинета — только manifest.

## CabinetUiModule (контракт)

| Поле / метод | Назначение |
|--------------|------------|
| `profileId` | Стабильный id |
| `supportedTabs` / destinations | Из manifest (или sync с ним) |
| `buildTab(tabId)` / router | Экраны pack |
| optional `buildContextActions` | Icon actions в host chrome |

Эквивалент будущего «remote entry»: один модуль — одна точка стыковки.

## Base cabinet UI (обязательный минимум)

Для `generic-assistant` и как baseline копирования:

| Surface | Назначение |
|---------|------------|
| Projects | EntityCollection проектов |
| Chat / workspace | Agent chat + attachments |
| Prompts | CRUD / версии модульных промптов |
| Skills | Управление skills |
| Rules | Управление rules |
| MCP | Конфиг servers/tools (⊆ allowlist) |
| Seed / files | Файлы для materialize |
| AGENTS | Always-on инструкции |

Доменный кабинет **добавляет** tabs, не удаляет base без явного флага в manifest.

## Default UIs

| profile_id | Screens |
|------------|---------|
| `generic-assistant` | Base surfaces выше |
| `equipment-procurement` | Base + Specs, Variants, KP, Equipment, … |

## Связь

- [packaging.md](packaging.md) · [default-cabinets.md](default-cabinets.md)
