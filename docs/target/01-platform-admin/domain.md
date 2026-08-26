# Platform Admin — domain

## Ответственность

Platform Admin = **платформенный** control plane.  
Company = **тот же паттерн**, локальный scope ([03](../03-companies/domain.md)).

1. Создаёт и сопровождает **Company** (+ KC орг-аккаунт).
2. Управляет **AI Provider Keys** `owner_scope=platform`; bind к компаниям (Company видит RO).
3. Квоты / feature flags кабинетов.
4. Oversight **всех** Project Containers; cascade via Project ([14](../14-project-containers/)).
5. **Назначает** кабинеты компаниям (Company — RO в MVP).
6. Агрегированный мониторинг.
7. Agent runtime policy (tool presets, model allowlists).
8. Break-glass к cabinet data — только с audit.
9. Не работает как Employee в том же chrome.

**Future (P-UNI-01):** Admin может создавать **локальных Employee без Company** и назначать им keys/cabinets напрямую — универсальные связи.

## Сущности

| Сущность | Описание |
|----------|----------|
| `Company` | Org + KC; локальный Admin-контур |
| `Employee` | Обычно через Company; future — direct Admin |
| `ProjectContainer` | Isolator; admin tab Контейнеры |
| `AiProviderKey` | platform-owned + bindings; видит также company-owned (надзор) |
| `CompanyAiKeyBinding` | platform key → company |
| `CompanyCabinetQuota` | Лимиты |
| `CompanyAgentRuntimePolicy` | preferred provider, tools, fallback |

## Операции

| Операция | Инвариант |
|----------|-----------|
| `company.create` | DB + KC; optional invite |
| `company.delete` | Confirm → wipe вниз |
| `ai_key.*` (platform) | Модуль 02; bind companies |
| `cabinet.assign_to_company` | Company list RO |
| `containers.*` | Все проекты платформы |
| `metrics.*` / `agent_policy.*` | Oversight |

## Границы

- Admin API: `require_platform_admin`.
- Не редактирует company-owned keys **от имени Company UI**, но может надзирать/break-glass по политике.
- Содержимое cabinet schema / chat — break-glass + audit.
