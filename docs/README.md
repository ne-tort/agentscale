# Prodavan — документация

## Канон (Target)

**Единственный источник правды для новой архитектуры:** [target/](target/).

1. [Принципы](target/00-principles.md) · [Глоссарий target](target/00-glossary.md)
2. [Platform Admin](target/01-platform-admin/) · [AI Provider Keys](target/02-ai-provider-keys/)
3. [Companies](target/03-companies/) · [Employees](target/04-employees/)
4. [Cabinets](target/05-cabinets/) · [Projects & runtime](target/06-projects-runtime/)
5. [UI mobile core](target/07-ui-mobile-core/) · [Agent providers](target/08-agent-providers/)
6. [Gap map (target ↔ legacy ↔ код)](target/09-gap-map.md)

Политика legacy: [LEGACY.md](LEGACY.md).

---

## LEGACY (справочно)

Разделы ниже описывают **предыдущую** модель (Tenant / M00–M09 / desktop UI). Их **не расширять** новыми фичами без явной пометки. Новые требования — только в `target/`.

| Раздел | Путь | О чём |
|--------|------|--------|
| Глоссарий (legacy) | [00-glossary.md](00-glossary.md) | Superseded → [target/00-glossary.md](target/00-glossary.md) |
| Vision | [01-vision/](01-vision/) | Продукт, домен (tenant-centric) |
| Architecture | [02-architecture/](02-architecture/) | ADR, tenancy, cabinets, agent, MCP |
| Modules | [03-modules/](03-modules/) | M00–M09 |
| Frontend | [04-frontend/](04-frontend/) | Flutter; виджеты → [target/07](target/07-ui-mobile-core/) |
| Backend | [05-backend/](05-backend/) | FastAPI, ERD, RLS |
| Agent | [06-agent-runtime/](06-agent-runtime/) | Providers → [target/08](target/08-agent-providers/) |
| Infrastructure | [07-infrastructure/](07-infrastructure/) | k3s, Terraform, CI/CD (актуально для ops) |
| Migration | [08-migration/](08-migration/) | Commerce MVP → Prodavan |
| Checklists | [09-checklists/](09-checklists/) | Doc/Impl gates (исторические) |
| Implementation | [10-implementation/](10-implementation/) | Roadmap I0–I9, gaps |

Инфра-runbook'и в `07-infrastructure/` (k3d, Argo, runner) остаются рабочими для деплоя; доменная модель ролей/UI в них не канон.
