# Cabinets — packaging & isolation

## Решение (зафиксировано)

| Вопрос | Ответ |
|--------|--------|
| Кабинет = microservice сейчас? | **Нет.** Излишество на текущем этапе. |
| Где живёт кабинет? | **В монолите** (один API process, один Flutter app, один Postgres cluster). |
| Изоляция? | **Строгая** структурно, логически, файлово; своя схема/таблицы; общение с платформой **только через контракты**. |
| Готовность к microservice? | Контракты и границы **как будто** кабинет уже отдельный сервис — чтобы вынести BE/FE позже без переписывания домена. |

Микросервис остаётся **эволюционным путём**, не текущим деплоем. См. § «Эволюция».

---

## Модель: modular monolith + cabinet seams

```text
┌──────────────────────────── Platform host ────────────────────────────┐
│  Identity · Companies · Employees · Projects meta · Agent runtime     │
│  AI keys · Grants · Attachments meta · Triggers bus · UI shell/core   │
│                                                                         │
│   ┌─ SPI / UiModule registry (единственная «стыковка») ─────────────┐ │
│   │                                                                   │ │
│   │  ┌──────────────────┐  ┌──────────────────┐                     │ │
│   │  │ generic-assistant│  │ equipment-…      │  … packs            │ │
│   │  │ (base cabinet)   │  │ (extends base)   │                     │ │
│   │  │ own files+schema │  │ own files+schema │                     │ │
│   │  └──────────────────┘  └──────────────────┘                     │ │
│   └───────────────────────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────────────────────────┘
         Postgres cluster: platform schema + cabinet schemas (см. § БД)
```

Платформа **не знает** домен кабинета. Кабинет **не знает** чужие кабинеты и не лезет в platform tables напрямую (кроме явного read-модели через SPI context / platform queries, если контракт это даёт).

---

## Три оси изоляции (обязательны уже в монолите)

### 1. Файловая / структурная

| Слой | Канон путей (target) |
|------|----------------------|
| API pack | `apps/api/src/prodavan/cabinets/<profile_id>/` — **весь** домен кабинета здесь |
| FE pack | `apps/flutter/lib/cabinets/<profile_id>/` — **весь** UI кабинета здесь |
| Pack assets | `packages/cabinet-packs/<profile_id>/` — profile JSON, default prompts/skills, SQL migrate scripts |
| Platform | `…/platform/`, `…/api/v1/` (thin), `…/application/` — **без** импортов `cabinets.<x>.services` |

Запрещено:

- Класть домен кабинета в `application/services` платформы.
- Импортировать `cabinets.equipment_…` из `cabinets.generic_…` как «общую библиотеку домена» (кроме явного **shared base kit** — см. § Base).
- FE: `features/procurement` вне `cabinets/<id>/` — домен только под cabinet tree.

### 2. Логическая

- Единственный вход платформы в кабинет: **SPI** ([module-contract](module-contract.md)).
- Единственный вход UI shell в кабинет: **`CabinetUiModule`** ([frontend](frontend.md)).
- Нет shared mutable state между кабинетами.
- Нет прямых SQL JOIN'ов platform↔cabinet или cabinet↔cabinet.
- Secrets домена (напр. S4B) — только в scope кабинета; AI keys — только через platform resolve (кабинет не читает vault ключей ИИ).

### 3. Данные

Сейчас: **один Postgres cluster**, изоляция схемами (предпочтительно) или жёстким префиксом таблиц + запрет cross-schema SQL из чужого pack.

| Объект | Владелец |
|--------|----------|
| `platform` / public platform tables | Host: companies, employees, projects meta, grants, ai_keys, … |
| `cab_<profile_id>` schema (или `cab_<instance_id>`) | Только этот cabinet module + его `migrate` |
| Workspace FS проекта | Runtime контейнера; содержимое пишет `materialize_project` кабинета |

`migrate` кабинета трогает **только** свою schema. Platform Alembic — только platform tables ([alembic.md](../../07-infrastructure/alembic.md)).

Позже (microservice): schema → отдельная БД; SPI → HTTP; FE pack → отдельный bundle/remote module — **без смены доменных границ**.

---

## Что кабинет МОЖЕТ переиспользовать (платформа / core)

Разрешено и ожидаемо:

| BE | FE |
|----|-----|
| `SpiContext` (session, company/employee/project ids) | `lib/core/theme`, `lib/core/widgets` ([07](../07-ui-mobile-core/)) |
| Platform identity assertions (уже проверенный principal) | Auth/session от shell (токен, contour) |
| Object storage / attachment **metadata API** платформы | `AppScaffold`, EntityCollection, selectors |
| Agent trigger/attachment **contracts** | Router host slots из manifest |

Запрещено маскировать домен под «core»:

- Не тащить procurement widgets в `lib/core`.
- Не делать `AppS4bButton` в core.
- Не шарить ORM-модели кабинета A в кабинет B.

---

## Base cabinet = основа продукта

`generic-assistant` — **не** «пустая заглушка», а **полноценный независимый кабинет** и **эталон**, от которого клонируют новые.

```text
generic-assistant (base)
  projects · chat+attachments · prompts · skills · rules · MCP · seeds · AGENTS UI
       │
       ├── copy-on-create → equipment-procurement (+ domain tabs/tables)
       ├── copy-on-create → <future-cabinet>
       └── …
```

| Правило | Смысл |
|---------|--------|
| Base **независим** | Работает без доменных кабинетов; на нём проверяют platform UX |
| Новые кабинеты **копируют** base | DX: fork tree + сменить `profile_id` + достроить домен |
| Достройка, не наследование runtime | Не «dynamic subclass в одном процессе с общим state». Предпочтительно **copy pack** + опциональный **shared base kit** (стабильные helpers), версионируемый отдельно |
| Base surface обязателен | Доменный кабинет **не выкидывает** projects/chat/context UI без явного решения в manifest |

Shared base kit (если появится): только `cabinets/_base/` или `packages/cabinet-base/` — контрактные helpers (materialize helpers, prompt versioning), **без** доменных таблиц equipment. Доменные packs зависят от kit, kit **не** зависит от доменов.

---

## Контракты «как к микросервису» внутри монолита

Даже in-process:

1. **SPI — единственный BE API** кабинета для host (как публичный HTTP API будущего сервиса).
2. **Manifest — capability document** (tabs, commands, queries, MCP allowlist, trigger kinds).
3. **Versioning** — `version` + `min_platform_version`; breaking SPI = major.
4. **Idempotent migrate / materialize**.
5. **Import ban** платформы на internals pack (эквивалент network boundary).
6. **FE UiModule** — единственная точка регистрации (эквивалент remote entry).
7. **Никаких back-door** platform routes, которые обходят SPI и лезут в cabinet services.

In-process сейчас = **transport optimization** (function call вместо HTTP), не снятие границ.

---

## Эволюция в microservice (когда понадобится)

Триггеры (примеры): независимый релиз/scale домена, чужая команда на кабинет, другой язык runtime, тяжёлая изоляция данных.

| Сейчас (monolith) | Потом (service) |
|-------------------|-----------------|
| `cabinets/<id>/` in API image | Отдельный deploy / image |
| SPI function call | SPI over HTTP/gRPC (тот же surface) |
| Schema в общем Postgres | Своя БД (или логический server) |
| FE в том же Flutter app | Отдельный package / deferred component / remote UI — **тот же** `CabinetUiModule` contract |
| Registry in-process | Service discovery + catalog |

Критерий готовности: вынос **без** переписывания domain services и UI screens — только transport + config.

---

## DX: новый кабинет

1. Copy `generic-assistant` (BE + FE + pack assets) → новый `profile_id`.
2. Своя schema + `migrate`; свои tabs в manifest.
3. Зарегистрировать SPI + `CabinetUiModule` (одна строка catalog/registry).
4. Admin grant → Company assign → Employee.

Критерий: **ноль** правок platform domain; максимум — registry/catalog entry.

---

## Анти-паттерны

| Антипаттерн | Почему плохо |
|-------------|--------------|
| «Пока заимпортим service кабинета в platform router» | Ломает будущий вынос; расползание домена |
| Общие таблицы `offers` в public для всех кабинетов | Cross-cabinet coupling |
| FE feature вне `cabinets/<id>` | Потеря файловой изоляции |
| Microservice «на всякий случай» сейчас | Ops/complexity без dig |
| Base как abstract class с обязательным override 20 методов | Хрупкое наследование; лучше copy + kit |

## Связь

- [module-contract.md](module-contract.md) — SPI frozen  
- [default-cabinets.md](default-cabinets.md) — base + equipment  
- [frontend.md](frontend.md) · [backend.md](backend.md)  
- [06 container](../06-projects-runtime/container.md)
