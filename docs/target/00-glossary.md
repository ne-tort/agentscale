# Глоссарий (target)

Канонический словарь. Legacy-термины — только для маппинга к старому коду/docs.

---

## Роли и контуры UI

| Термин | Определение | Legacy-аналог |
|--------|-------------|---------------|
| **Platform Admin** | Оператор платформы. Свой UI. Компании, AI-ключи, квоты/policy кабинетов, optional starter bundles, кросс-мониторинг. | `platform.admin` |
| **Company** | Организация-клиент (org). Свой UI-контур для `company.admin`. **Не** User. | Tenant |
| **Employee** | Человек с `keycloak_sub`; membership в Company; работа в cabinets. | `tenant.member` / operator |
| **Company account** | Employee с ролью `company.admin` (открывает Company UI). | Tenant admin user |

Один физический человек может иметь разные контуры (редко); в продукте контур определяется ролью при логине.

---

## Домен продукта

| Термин | Определение |
|--------|-------------|
| **Prodavan** | Универсальный облачный SaaS автоматизации задач агентами (не только закупки). |
| **Cabinet** | Динамический instance: schema + meta (tables/tabs/views/MCP) + data; UI из метаданных; export/import. |
| **Base cabinet** | Обязательный шаблон instance (projects, chat, context, Tables, Tools + `cabinet.*` contracts). |
| **Cabinet bundle** | Переносимый zip/json артефакт meta(+seed); import = новая копия. |
| **Cabinet module** | *(устарело как code-pack)* → см. Cabinet Runtime + bundle. |
| **Cabinet ownership** | Employee (operate) + Company (org) + Platform Admin (oversee); peers isolated. |
| **MCP package** | Agent-built zip (code + mcp manifest), deployed via `cabinet.mcp_packages.deploy`, reused across projects of the cabinet. |
| **Meta catalog** | Системные таблицы описания схемы/UI/MCP внутри instance. |
| **Dynamic tab** | Вкладка UI, зарегистрированная в meta.tabs, не Flutter-feature. |
| **Cabinet allowlist** | *(legacy)* → квоты + optional starter bundle catalog. |
| **Project (unit)** | Изолированная единица работы внутри кабинета; агент может мутировать cabinet meta через MCP. |
| **Materialize** | Сборка workspace проекта из кабинета (prompts/skills/MCP registry → FS). |
| **Trigger** | Событие, запускающее/продолжающее агента (сообщение чата, webhook кабинета, cron…). |
| **AI Provider Key** | Сущность ключа доступа к ИИ-провайдеру с профилем, сроками и привязками к компаниям. |
| **api_kind** | Тип интеграции ключа: `cursor_sdk`, `openai_api`, `openrouter`, `anthropic_api`, `cli_subscription`, … |
| **provider** | Продуктовый провайдер минимального набора: `cursor`, `codex`, `claude_code` (+ расширяемо). |

---

## UI (mobile core)

| Термин | Определение |
|--------|-------------|
| **AppListItem** | Единый элемент списка во всём приложении. |
| **AppSelectorPage** | Полноэкранная страница выбора (single/multi); замена dropdown/modal. |
| **Modal ban** | Запрет диалогов, bottom sheets, popup menus для выбора и confirm. |

---

## Runtime / агенты

| Термин | Определение |
|--------|-------------|
| **AgentProviderPort** | Абстракция запуска агента (create/resume/stream/cancel). |
| **Cursor SDK adapter** | Primary реализация порта через `@cursor/sdk`. |
| **OpenClaw** | Исторический концепт в legacy-docs; **не** используется как runtime в Prodavan. |

---

## Подписка и метрики

| Термин | Определение |
|--------|-------------|
| **Company subscription** | Срок доступа компании к сервису Prodavan; может быть **бессрочной**. |
| **Usage metrics** | Счётчики потребления: сотрудники, проекты, токены, сообщения агента, storage… |
