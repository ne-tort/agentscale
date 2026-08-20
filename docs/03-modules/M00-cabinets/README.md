# M00 — Кабинеты (Cabinets)

Модуль **M00-cabinets** — фундамент платформы Prodavan. Он определяет изолированные рабочие контексты («кабинеты»), привязывает к каждому кабинету **профиль задач** (capabilities), управляет жизненным циклом кабинета и обеспечивает безопасное переключение активного контекста для оператора и агента.

## Назначение

| Задача | Описание |
| --- | --- |
| CRUD кабинетов | Создание, чтение, обновление метаданных, мягкое удаление (архивация) |
| Реестр профилей | Каталог поддерживаемых профилей (`electronics-procurement`, `legal-docs`, …) |
| Схема capabilities | Декларативное описание того, что кабинет **умеет** и **не умеет** |
| Pack seed pipeline | Первичная инициализация файлов кабинета из шаблона профиля |
| Cabinet switch API | Атомарная смена активного кабинета с валидацией прав и инвалидацией кэша |
| Cross-cabinet negative tests | Набор регрессионных тестов: данные одного кабинета недоступны из другого |

## Границы модуля

**Входит:**

- Сущность `Cabinet`, `CabinetProfile`, `CapabilitiesSchema`
- API `/v1/cabinets`, `/v1/cabinets/{cid}/switch`
- Таблицы `cabinets`, `cabinet_profiles`, `cabinet_capabilities`
- Seed-пакеты в `packages/cabinet-packs/<profile-id>/`
- Middleware изоляции `cab:*` на всех downstream-модулях

**Не входит:**

- Проекты внутри кабинета → **M01-projects**
- Спеки и КП → **M02-specs-kp**
- Промпты и AGENTS.md → **M03-prompts**
- Каталоги и S4B → **M04-catalogs** (S4B **только** для `electronics-procurement`)

## Ключевые инварианты

1. Каждый кабинет принадлежит ровно одному tenant (`tid`).
2. Профиль кабинета **не меняется** после создания (immutable `profile_id`); для другого профиля — новый кабинет.
3. Capabilities вычисляются из профиля + overrides; downstream-модули **не** дублируют логику «есть ли S4B» — читают `capabilities.s4b`.
4. Workspace key всех дочерних сущностей начинается с `cab:{tid}:{cid}:…` (см. M01).
5. Переключение кабинета не переносит незакоммиченные черновики между контекстами.

## Зависимости

```text
M00-cabinets
  ← M-identity (tenant, user, RBAC)     [внешний]
  → M01-projects
  → M03-prompts (seed AGENTS.md, profiles/)
  → M04-catalogs (инициализация user catalogs; system S4B — conditional)
```

## Артефакты документации

| Файл | Содержание |
| --- | --- |
| [domain.md](domain.md) | Сущности, состояния, инварианты |
| [api.md](api.md) | REST-контракт, примеры запросов |
| [persistence.md](persistence.md) | DDL, индексы, миграции |
| [storage.md](storage.md) | Файловая структура кабинета на диске |
| [mcp-tools.md](mcp-tools.md) | MCP для агента: list/switch/describe cabinet |
| [ui.md](ui.md) | Экраны выбора и настройки кабинета |
| [security.md](security.md) | Изоляция, RBAC, cross-cabinet |
| [checklist-implementation.md](checklist-implementation.md) | Чеклист разработки |
| [checklist-review.md](checklist-review.md) | Чеклист ревью и QA |

## Профиль electronics-procurement vs остальные

| Capability | `electronics-procurement` | Другие профили |
| --- | --- | --- |
| `specs_kp` | ✓ | по профилю |
| `equipment_cards` | ✓ | ✗ |
| `s4b` | ✓ | **✗ всегда** |
| `catalogs_user` | ✓ | по профилю |
| `catalogs_system_s4b` | ✓ | **✗ всегда** |

> **Жёсткое правило:** S4B и system-database `s4b-cache` доступны **исключительно** в кабинетах с профилем `electronics-procurement`. Любая попытка включить S4B через override отклоняется с `CAPABILITY_FORBIDDEN`.

## Статус

| Версия документа | 0.1.0-draft |
| --- | --- |
| Модуль | M00 |
| Язык | ru |
