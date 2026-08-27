# Target architecture — канон

Документация **целевой** архитектуры Prodavan. Legacy: [LEGACY](../LEGACY.md).  
Агенту: [AGENTS.md](../../AGENTS.md). Код может отставать — канон важнее stub.

## Суть

Admin (KC) → Company (KC, **локальный Admin**: employees / containers / own AI keys) → Employee → Cabinet → Project → Pod.  
Фокус сущности: [03-companies](03-companies/). Gaps: [09-gap-map](09-gap-map.md).

## Читать сначала

1. **[Сущности и иерархия](00-entities.md)** ← карта продукта  
   Lifecycle (pause / soft-delete / purge): [00-lifecycle.md](00-lifecycle.md)  
   Relations (связи): [00-relations.md](00-relations.md)
2. [Принципы](00-principles.md) · [Глоссарий](00-glossary.md)  
3. [Cabinets](05-cabinets/) · [Projects](06-projects-runtime/) · [**Containers / Pods**](14-project-containers/) · [**Content storage**](15-content-storage/)  
4. [Identity](10-identity-keycloak/) · [Admin](01-platform-admin/) · [AI Keys](02-ai-provider-keys/)  
5. [UI](07-ui-mobile-core/) · [Agents](08-agent-providers/) · [Infra](13-platform-infra/)  
6. [Gap](09-gap-map.md) · [As-built](12-layer-docs/) · [Plan](11-implementation-plan/)

## Карта модулей

| ID | Модуль | Суть |
|----|--------|------|
| 00 | Entities | Иерархия и краткие определения |
| 10 | Identity | Keycloak OIDC |
| 01 | Platform Admin | Компании, ключи, контейнеры |
| 02 | AI Provider Keys | Ключи Cursor/Codex/Claude |
| 03 | Companies | Org, quotas, policy |
| 04 | Employees | Работа в кабинетах |
| 05 | Cabinets | Оболочка + module data |
| 06 | Modules | Reusable meta · [meta-syntax](06-modules/meta-syntax/) |
| — | [Projects runtime](06-projects-runtime/) | Unit работы, triggers, chat |
| 07 | UI mobile core | M3, EntityCollection |
| 08 | Agent providers | SDK adapters |
| 09 | Gap map | канон ↔ код |
| 11 | Implementation plan | Слои / DoD |
| 12 | Layer docs | As-built |
| 13 | Platform infra | Kafka, MinIO, Celery, Redis |
| 14 | Project Containers | **Isolated Pod** per Project |
| 15 | Content storage | File Service + assets/aliases/ACL |
