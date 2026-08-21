# Глоссарий (target)

Канонический словарь. Legacy-термины — только для маппинга к старому коду/docs.

---

## Роли и контуры UI

| Термин | Определение | Legacy-аналог |
|--------|-------------|---------------|
| **Platform Admin** | Оператор платформы. Свой UI. Компании, AI-ключи, allowlist кабинетов, кросс-мониторинг. | `platform.admin` |
| **Company** | Организация-клиент (org). Свой UI-контур для `company.admin`. **Не** User. | Tenant |
| **Employee** | Человек с `keycloak_sub`; membership в Company; работа в cabinets. | `tenant.member` / operator |
| **Company account** | Employee с ролью `company.admin` (открывает Company UI). | Tenant admin user |

Один физический человек может иметь разные контуры (редко); в продукте контур определяется ролью при логине.

---

## Домен продукта

| Термин | Определение |
|--------|-------------|
| **Cabinet** | Специализированное рабочее пространство (модуль): свой UI, БД, промпты, MCP, tools. |
| **Cabinet module** | Подключаемый пакет BE+FE, реализующий контракт платформы. |
| **Cabinet allowlist** | Список кабинетов, которые Admin выдал компании; Company раздаёт их сотрудникам. |
| **Project (unit)** | Изолированная единица работы внутри кабинета; контрактная сущность для runtime. |
| **Project container** | Runtime-изоляция проекта (pod/container): FS, agent, MCP, seed files. |
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
