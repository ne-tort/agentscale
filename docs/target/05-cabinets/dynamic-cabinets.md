# Dynamic cabinets — смена концепции (канон)

> **Supersedes** прежнюю модель «статический code-pack на каждый домен (`equipment-procurement` как Flutter/Python модуль)».  
> Статичность остаётся только у **платформенного runtime** кабинета и у **базового шаблона**. Домен = данные + метаданные + MCP-определения.

## Суть одной фразой

**Кабинет** — динамическая, импортируемая/экспортируемая сущность сотрудника: изолированное хранилище + каталог таблиц/вкладок/MCP + UI, который **строится из метаданных**, а не из захардкоженных экранов. ИИ в проекте достраивает кабинет через **контрактные MCP** (таблицы, вкладки, инструменты), и результат можно переиспользовать в других проектах и у других сотрудников.

```text
Company ──owns (org)──► CabinetInstance ◄──creates/edits── Employee
                              ▲
                              └── oversees ── Platform Admin
        peers cannot read each other's schemas
```

---

## Решения (зафиксировано по ответам)

| Тема | Решение |
|------|---------|
| **Ownership** | Кабинет принадлежит одновременно **Employee** (создатель/редактор), **Company** (орг-владение, метрики, политика) и виден **Platform Admin** (надзор, квоты, break-glass). Сотрудники **не** видят чужие instances. |
| **Import** | Всегда **deep copy** → новый `cabinet_id` / новая schema; не live-link на чужие данные |
| **Catalog** | Employee создаёт свободно в рамках квот. Platform/Company держат **опциональный** catalog starter bundles (удобство, не гейт). Модерация bundle’ов компании — later, не блокер v1 |
| **Custom MCP** | ИИ **разрабатывает** MCP-пакет (скрипты/файлы + manifest) и сдаёт через **унифицированный platform tool** (`cabinet.mcp_packages.deploy` zip) со строгим контрактом. Пакет переиспользуется в проектах кабинета. См. [mcp-packages.md](mcp-packages.md) |
| **Изоляция данных** | **Schema per CabinetInstance** (`cab_inst_<id>`). Копии схем независимы: правка своей не трогает чужие. Employee A не имеет доступа к schema Employee B |

---

## Что меняется vs старая модель

| Было | Стало |
|------|--------|
| Кабинет = code module (BE+FE pack) | Кабинет = **instance data product** |
| Доменные вкладки в Flutter `cabinets/equipment_…` | Вкладки из **meta.tabs** + generic renderers |
| Новый кабинет = копировать код | Новый = clone Base / import **bundle** |
| SPI доменных commands в Python pack | **Cabinet Runtime SPI** платформы + user MCP defs |
| Admin allowlist profile_id | Admin: квоты/feature; Employee **создаёт** кабинеты |
| «Подбор оборудования» = отдельный модуль | **Starter bundle** (seed tables+meta+MCP) в каталоге |

Изоляция monolit→service ([packaging](packaging.md)) **сохраняется** для *runtime*, не для доменных packs кода.

---

## Профессиональные паттерны (следовать)

### 1. Metadata-driven UI (Schema + UI Schema)

Как Retool / Appsmith / RJSF / Formily:

- **Data schema** — что хранится (поля, типы, required).
- **UI schema / view meta** — как показать (tab title, columns, widgets, order, density).
- Frontend = **interpreters**, не экраны домена.

Prodavan: `TableDefinition` + `ViewDefinition` → `AppEntityCollection` / dynamic form.

### 2. System catalog (meta) vs user data

Как `information_schema`, но **продуктовый** каталог:

| Catalog | Data |
|---------|------|
| `meta.tables`, `meta.columns`, `meta.tabs`, `meta.views`, `meta.mcp_tools` | `data.<table>` или JSONB documents |

Агент и UI **никогда** не пишут в meta сырым SQL без контракта — только через MCP/API.

### 3. Controlled DDL / Capability MCP (не freeform SQL)

Паттерн **capability-safe mutation**:

- MCP `cabinet.tables.create` с JSON schema аргументов (имя, columns[]).
- Запрет произвольного `EXECUTE` DDL от модели.
- Валидация имён, типов из allowlist (`text`, `number`, `bool`, `json`, `ref`, `datetime`, `file_ref`).

Иначе ИИ легко ломает изоляцию и RLS.

### 4. Portable Cabinet Bundle (export/import)

Паттерн **artifact / package** (близко к dbt project + app export):

```text
cabinet.bundle.zip
  manifest.json
  meta/*.json            # tables, tabs, views, mcp_tools
  mcp_packages/*.zip     # custom MCP packages
  data/                  # optional seed rows
  README.md
```

Идемпотентный import → новый instance.

### 5. Tool Registry + MCP Packages

Два уровня tools:

1. **Platform `cabinet.*`** — всегда; DDL/UI/rows/bundle.  
2. **User MCP packages** — zip (Python/scripts + `mcp.json` contract), которые агент **собирает и деплоит** через строгий install tool; затем подключаются к проектам кабинета.

См. [mcp-packages.md](mcp-packages.md).

### 6. Template Method — Base Cabinet

Base = обязательный starter:

- Системные tabs: Projects, Chat, Prompts, Skills, Rules, **Tables**, **MCP tools**, AGENTS/Seeds.
- Контракты MCP уже подключены.
- Пользователь/ИИ **добавляют** tabs/tables, не выкидывая base.

### 7. Multi-tenant data plane

CabinetInstance = tenant данных:

- Schema-per-instance (сейчас).
- Позже: DB-per-instance / microservice без смены bundle format.

### 8. Agent-as-builder + Policy firewall

Агент в project container = builder экосистемы, но:

- Tool policy Admin ([08 permissions](../08-agent-providers/permissions-policy.md)).
- Cabinet MCP allowlist.
- Audit каждой meta-mutation.

---

## Модель данных (логическая)

### Platform (host)

| Сущность | Назначение |
|----------|------------|
| `CabinetInstance` | id, `owner_employee_id`, `company_id`, name, base_version, status |
| ACL | Employee (CRUD своих), Company admin (org view/metrics/policy), Platform admin (all + break-glass) |
| Quotas | max cabinets / tables / mcp packages / storage / package size |

### Peer isolation

| Правило | Смысл |
|---------|--------|
| Schema per instance | `cab_inst_<id>` только для этого кабинета |
| No cross-read | Employee не SELECT/MCP в чужой schema |
| Copy on import/export | Новая schema; изменения локальны |
| Company view | Метаданные/метрики/policy — не «правка строк чужого кабинета» по умолчанию (break-glass + audit) |

### Per-instance meta (одинаковая структура у всех)

| Таблица meta | Назначение |
|-------------|------------|
| `tables` | slug, label, description, storage_kind |
| `columns` | table_id, name, type, required, ref_table? |
| `tabs` | id, title, order, view_id, icon? |
| `views` | table_id, kind=`collection`\|`form`\|`board`, ui_json |
| `mcp_tools` | declarative wrappers |
| `mcp_packages` | deployed custom MCP artifacts |
| `mcp_bindings` | which tools/packages enabled for projects |

### Data

- Default: **physical tables** created by runtime по meta (проще SQL/MCP query).
- Альтернатива для сверхгибких сущностей: JSONB document store + meta.columns как soft schema — допустим `storage_kind=json_document`.

Рекомендация v1: physical tables для «список поставщиков»; json_document для «свободные карточки».

---

## Dynamic UI (Flutter)

Один shell:

```text
CabinetShell
  tabs ← meta.tabs (EntityCollection of tabs + fixed base tabs)
  for tab in dynamic:
    load view meta + query rows
    render AppEntityCollection / AppForm from ui_json
```

Правила:

- Только core widgets ([07](../07-ui-mobile-core/)).
- `ui_json` декларативен (column show/hide, title field, subtitle fields, tone from column).
- Нет `equipment_procurement` feature module как канон домена.

«Список разрешенных поставщиков» / «Характеристики оборудования» = rows + meta, не отдельные Dart-экраны.

---

## MCP contracts (platform-implemented)

ИИ и UI вызывают **платформенный** набор `cabinet.*` (DDL/UI/rows/bundle) — см. [mcp-contracts.md](mcp-contracts.md).

Отдельно: агент **разрабатывает** пользовательский MCP (скрипты + файлы + manifest), упаковывает в zip и сдаёт через строгий tool `cabinet.mcp_packages.deploy` — см. [mcp-packages.md](mcp-packages.md). После деплоя пакет появляется в registry и подключается к проектам кабинета (мутации своих таблиц, вызовы внешних сервисов в рамках sandbox/network policy).

Декларативные wrappers (`rows_upsert` …) остаются для простых tools без своего кода.

---

## Жизненный цикл

```text
Employee: «Создать кабинет» → clone Base
  → create Project → agent materialize (+ platform cabinet.* MCP)
  → user: «сделай вкладку поставщиков и tool под API поставщика»
  → agent: tables/tabs/views.create
  → agent: пишет package (python + mcp.json), zip
  → agent: cabinet.mcp_packages.deploy(zip)
  → package в registry → доступен другим projects этого кабинета
  → Export cabinet bundle (+ packages) → коллега Import (копия)
```

---

## Роли (ownership)

| Роль | Права на CabinetInstance |
|------|--------------------------|
| **Employee** (создатель) | Полный CRUD meta/data, export/import, деплой MCP packages, проекты |
| **Company** (org) | Владение на уровне организации: метрики, квоты, policy, список кабинетов сотрудников; не читать чужие data rows по умолчанию |
| **Platform Admin** | Надзор всех компаний: квоты, feature flags, audit, break-glass (аудит обязателен) |
| Peer Employee | **Нет доступа** к schema/data чужого instance |

Старый «Admin allowlist profile_id модулей» → не основной путь. Starter bundles catalog — optional.

---

## Безопасность (жёстко)

1. DDL/DML схемы кабинета — через typed `cabinet.*` или код **только внутри** deployed package sandbox (не произвольный SQL от модели в chat).  
2. Schema isolation per instance — peer employees isolated.  
3. MCP packages: только через deploy contract; validate zip; run in sandbox; network/egress по Admin AgentToolPolicy.  
4. Cabinet bundle export: data+meta+package **sources**; secrets stripped.  
5. Audit: meta changes, package deploy, imports, break-glass.  
6. Company/Admin org view ≠ silent data exfiltration.

---

## Миграция мышления «подбор оборудования»

| Старый артефакт | Новый |
|-----------------|--------|
| `electronics_procurement` Python | Bundle: tables runs/lineitems/offers + MCP search wrappers + views |
| Flutter procurement screens | views/ui_json на EntityCollection |
| Pack prompts в git | rows в cabinet prompts / seed в bundle |

Платформа может поставлять **official starter bundles** в read-only catalog (не code packs).

---

## Связанные документы

- [architecture.md](architecture.md) — слои runtime  
- [meta-and-ui.md](meta-and-ui.md) — схемы meta/ui_json  
- [mcp-contracts.md](mcp-contracts.md) — platform `cabinet.*`  
- [mcp-packages.md](mcp-packages.md) — **custom MCP zip deploy**  
- [bundle-format.md](bundle-format.md) — export/import кабинета  
- [packaging.md](packaging.md) — изоляция schema  
- [default-cabinets.md](default-cabinets.md) — Base + starters  

## Открытые решения (неблокирующие)

- JSONB vs physical DDL primary для `storage_kind`  
- Realtime UI refresh (poll vs websocket)  
- Semver / migration Base template upgrades  
- Точный sandbox runtime для packages (gVisor / bubblewrap / micro-VM) — выбрать на code-wave  
