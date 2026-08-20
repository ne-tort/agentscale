# Prodavan — документация

Модульная спецификация платформы. Каждый модуль — папка с 10 файлами: domain, api, persistence, storage, mcp-tools, ui, security, checklists.

## Как читать

1. [Глоссарий](00-glossary.md)
2. [Product vision](01-vision/product-vision.md) → [Domain model](01-vision/domain-model.md)
3. [Architecture overview](02-architecture/overview.md)
4. Модули [M00–M09](03-modules/README.md) по порядку зависимостей
5. [Frontend](04-frontend/architecture.md) · [Backend](05-backend/structure.md) · [Agent runtime](06-agent-runtime/providers.md) · [Infra](07-infrastructure/topology.md)
6. [Migration from Commerce](08-migration/commerce-boundary.md)
7. [Checklists & progress](09-checklists/PROGRESS.md)

## Карта разделов

| Раздел | Путь | О чём |
|--------|------|--------|
| Vision | [01-vision/](01-vision/) | Продукт, домен |
| Architecture | [02-architecture/](02-architecture/) | ADR, tenancy, cabinets, agent, MCP, threat model |
| Modules | [03-modules/](03-modules/) | M00–M09 функциональные модули |
| Frontend | [04-frontend/](04-frontend/) | Flutter, design system, widgets |
| Backend | [05-backend/](05-backend/) | FastAPI, ERD, RLS, Alembic |
| Agent | [06-agent-runtime/](06-agent-runtime/) | Providers, isolation, prompts |
| Infrastructure | [07-infrastructure/](07-infrastructure/) | k3s, Terraform, CI/CD, runner |
| Migration | [08-migration/](08-migration/) | Commerce MVP → Prodavan |
| Checklists | [09-checklists/](09-checklists/) | PROGRESS, gates, review |

## Приёмка документации

- Обязательный пункт: **≥ 8/10** (см. шкалу в [PROGRESS.md](09-checklists/PROGRESS.md))
- Среднее по итерации: **≥ 8.5/10**
- После каждой фазы: [review-protocol.md](09-checklists/review-protocol.md)
