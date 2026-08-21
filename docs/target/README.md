# Target architecture — канон

Документация **целевой** архитектуры Prodavan. Всё в `docs/01`…`10` — [LEGACY](../LEGACY.md).

## Порядок чтения

1. [Принципы](00-principles.md)
2. [Глоссарий](00-glossary.md)
3. [Identity / Keycloak](10-identity-keycloak/) ← session + entitlements
4. [Platform Admin](01-platform-admin/) → [AI Provider Keys](02-ai-provider-keys/)
5. [Companies](03-companies/) → [Employees](04-employees/)
6. [Cabinets](05-cabinets/) → [Projects & runtime](06-projects-runtime/)
7. [UI mobile core](07-ui-mobile-core/) (+ [ux-system](07-ui-mobile-core/ux-system.md))
8. [Agent providers](08-agent-providers/)
9. [Gap map](09-gap-map.md) ← решённые противоречия

## Карта модулей

| ID | Модуль | Суть |
|----|--------|------|
| 10 | Identity (Keycloak) | OIDC IdP; JWKS; без локального password-login |
| 01 | Platform Admin | UI админа: компании, ключи ИИ, мониторинг, allowlist кабинетов |
| 02 | AI Provider Keys | Унифицированные ключи Cursor / Codex / Claude + профили |
| 03 | Companies | Компания как контур пользователя: сотрудники, кабинеты, метрики |
| 04 | Employees | Сотрудник: выбор кабинета → работа в кабинете |
| 05 | Cabinets | Изолированные модули BE+FE; default: универсальный + подбор оборудования |
| 06 | Projects & runtime | Project unit, контейнер, триггеры, чат с вложениями |
| 07 | UI mobile core | Material 3, без модалок, AppListItem / AppSelectorPage |
| 08 | Agent providers | SDK-вердикты: Cursor / Codex / Claude; GLM вне scope |
| 09 | Gap map | target ↔ legacy ↔ текущий код |
