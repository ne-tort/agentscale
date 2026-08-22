# Target architecture — канон

Документация **целевой** архитектуры Prodavan. Всё в `docs/01`…`10` — [LEGACY](../LEGACY.md).  
Код `apps/*` — [STUB](../../STUB.md); инструкции агенту — [AGENTS.md](../../AGENTS.md).

## Суть (одной строкой)

Универсальный облачный SaaS автоматизации задач: Admin → Company → Employee → **Cabinet** (модуль) → **Project** (контейнер агента с промптами/skills/rules/MCP из UI кабинета).

## Порядок чтения

1. [Принципы](00-principles.md) ← суть продукта + 6 правил
2. [Глоссарий](00-glossary.md)
3. [Identity / Keycloak](10-identity-keycloak/) ← session + entitlements
4. [Platform Admin](01-platform-admin/) → [AI Provider Keys](02-ai-provider-keys/)
5. [Companies](03-companies/) → [Employees](04-employees/)
6. [Cabinets](05-cabinets/) → [Projects & runtime](06-projects-runtime/)
7. [UI mobile core](07-ui-mobile-core/) (+ [ux-system](07-ui-mobile-core/ux-system.md))
8. [Agent providers](08-agent-providers/) (+ [workspace-context](08-agent-providers/workspace-context.md))
9. [Gap map](09-gap-map.md)

## Карта модулей

| ID | Модуль | Суть |
|----|--------|------|
| 10 | Identity (Keycloak) | OIDC IdP; JWKS; без локального password-login |
| 01 | Platform Admin | UI админа: компании, ключи ИИ, мониторинг, allowlist кабинетов |
| 02 | AI Provider Keys | Унифицированные ключи Cursor / Codex / Claude + профили |
| 03 | Companies | Иерархия org: сотрудники, grants кабинетов, метрики |
| 04 | Employees | Сотрудник: выбор кабинета → работа в кабинете |
| 05 | Cabinets | Modular monolith + строгие швы; base copy DX; schema-per-cabinet |
| 06 | Projects & runtime | Project unit, контейнер, materialize, триггеры, чат с вложениями |
| 07 | UI mobile core | Material 3, EntityCollection, laconic, без модалок |
| 08 | Agent providers | SDK + паритет AGENTS/skills/rules/MCP |
| 09 | Gap map | target ↔ legacy ↔ stub-код |
