# Platform Admin — domain

## Ответственность

Platform Admin:

1. Создаёт и сопровождает **Company**.
2. Управляет **AI Provider Keys** (модуль 02).
3. Задаёт **квоты / feature flags** динамических кабинетов (max instances, tables, MCP packages, storage).
4. Опционально ведёт **catalog starter bundles** (удобство, не обязательный гейт создания).
5. Видит **агрегированный мониторинг** (компании, usage, cabinet counts).
6. Управляет **agent runtime policy**: tool presets, model allowlists, quotas ([08](../08-agent-providers/admin-control-plane.md)).
7. Break-glass к cabinet data — только с audit; в обычном режиме не читает workspace/rows.
8. Не работает как Employee в том же chrome.

См. кабинеты: [05 dynamic](../05-cabinets/dynamic-cabinets.md).

## Сущности (платформенный контур)

| Сущность | Описание |
|----------|----------|
| `Company` | Организация: slug, name, subscription, status |
| `CompanySubscription` | `ends_at` nullable = бессрочно; `plan` опционально |
| `StarterBundleCatalogEntry` | Опциональный official/company starter (`cabinet.bundle`) |
| `CompanyCabinetQuota` | Лимиты на create/import cabinets & packages |
| `AiProviderKey` | См. модуль 02 |
| `CompanyAiKeyBinding` | M:N: ключ ↔ компании |
| `AgentToolPolicy` / presets | FS/shell/network/MCP caps |
| `ModelAllowlist` | Разрешённые model ids |
| `CompanyAgentRuntimePolicy` | preferred provider, tool preset, quotas, fallback |

**Устарело как канон:** `CabinetCatalogEntry` / `CompanyCabinetGrant` по статическим `profile_id` code-modules.

## Операции

| Операция | Инвариант |
|----------|-----------|
| `company.create` | Уникальный slug; invite company.admin (email → Keycloak) |
| `company.suspend` / `activate` | Suspend → block employee login |
| `company.set_subscription` | Дата или `lifetime=true` |
| `company.set_cabinet_quotas` | Лимиты dynamic cabinets |
| `starter_bundles.*` | Optional catalog CRUD |
| `ai_key.*` | Модуль 02 |
| `agent_policy.*` | Tool/model/quota defaults |
| `models.sync` / `models.allowlist` | Catalog |
| `metrics.*` | Companies, agent usage, cabinet counts |

## Границы доступа

- Admin API: `require_platform_admin`.
- Содержимое cabinet schema / chat — только break-glass + audit.
