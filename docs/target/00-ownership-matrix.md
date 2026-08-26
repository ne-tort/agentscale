# Ownership matrix (канон)

Единая модель владения для platform-owned vs company-owned сущностей.  
См. также: [02-ai-provider-keys/domain.md](02-ai-provider-keys/domain.md), [05-cabinets/assignment.md](05-cabinets/assignment.md).

## Derived flags (API)

| Flag | Смысл |
|------|--------|
| `owner_scope` | `platform` \| `company` |
| `writable` | actor может менять registry / secrets-equivalent |
| `operable` | Employee может работать (active assignment) |
| `source` | `platform_assigned` \| `company_local` |

## Матрица

| Entity | owner_scope | Grants | Who writes | Who reads secrets |
|--------|-------------|--------|------------|-------------------|
| AiProviderKey | platform/company | company bindings (+ owner_company_id) | Admin; Company if company-owned | Company only if company-owned |
| Cabinet | platform/company | company N:M + employee N:M | Company if company-owned; Admin always | meta RO if platform-owned |
| Project | via cabinet | cabinet assignment | assigned operators | — |
| Company | platform | — | Admin | — |
| Employee | company | cabinet assignments | Company admin | — |

## Cabinets

| Создатель | owner_scope | Company | Employee |
|-----------|-------------|---------|----------|
| Admin | `platform` | use + assign employees; **no meta edit** | operate via assignment |
| Employee | `company` | full CRUD meta/registry in own company | operate via assignment |

## AI keys

| owner_scope | Company UI |
|-------------|------------|
| `platform` + binding | use; `writable=false` (no rotate/secret) |
| `company` | full manage for owner company |

Cascade/metrics учитывают `owner_company_id` для company-owned keys без explicit binding row.
