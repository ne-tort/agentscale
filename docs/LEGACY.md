# LEGACY — политика документации

## Что считается legacy

Всё под `docs/01-vision/` … `docs/10-implementation/`, а также корневой [`00-glossary.md`](00-glossary.md), является **legacy** относительно канона [`target/`](target/).

Исключение по смыслу (не по домену продукта): операционные runbook'и в `07-infrastructure/` (k3s, Terraform, local E2E) — продолжают использоваться для инфраструктуры.

## Правила

1. **Новые продуктовые требования** описывать **только** в `docs/target/`.
2. Legacy-файлы **не удалять** (история, ссылки из кода/PR).
3. При правке legacy — в начале файла баннер:

   ```markdown
   > **LEGACY.** Канон: [docs/target/…](…). Не расширять без синхронизации с target.
   ```

4. Термины **Tenant / tenant.member / Operator** в legacy ≈ **Company / Employee** в target (см. [target/00-glossary.md](target/00-glossary.md)).
5. Старый widget-catalog и desktop shell **не** определяют UI; канон — [target/07-ui-mobile-core/](target/07-ui-mobile-core/) (mobile-first, без модалок).

## Карта supersede

| Legacy | Target |
|--------|--------|
| `00-glossary.md` | `target/00-glossary.md` |
| `01-vision/*`, `02-architecture/overview.md` | `target/00-principles.md` |
| `M08-tenants`, multi-tenancy | `01-platform-admin`, `03-companies`, `04-employees` |
| `M08` JWT / password login | `10-identity-keycloak` (Keycloak OIDC) |
| Secrets / env API keys | `02-ai-provider-keys` |
| `M00-cabinets`, cabinet-spi | `05-cabinets` |
| `M01-projects`, agent-isolation | `06-projects-runtime` |
| `04-frontend/widget-catalog.md` | `07-ui-mobile-core` |
| `06-agent-runtime/*` | `08-agent-providers` |
| `10-implementation/LOGICAL-GAPS.md` | `09-gap-map.md` |
