# Platform Admin — domain

## Ответственность

Platform Admin:

1. Создаёт и сопровождает **Company**.
2. Управляет сущностями **AI Provider Key** (см. модуль 02).
3. Назначает компании **allowlist кабинетов** (какие cabinet modules доступны).
4. Видит **агрегированный мониторинг** по всем компаниям.
5. Управляет **agent runtime policy**: tool presets, model allowlists, quotas ([08 admin-control-plane](../08-agent-providers/admin-control-plane.md)).
6. Не работает внутри кабинета как Employee (нет смешения контуров).

## Сущности (платформенный контур)

| Сущность | Описание |
|----------|----------|
| `Company` | Организация: slug, name, subscription, status |
| `CompanySubscription` | `ends_at` nullable = бессрочно; `plan` опционально |
| `CabinetCatalogEntry` | Зарегистрированный cabinet module (`profile_id`) |
| `CompanyCabinetGrant` | M:N: компания ↔ разрешённые кабинеты |
| `AiProviderKey` | См. модуль 02 |
| `CompanyAiKeyBinding` | M:N: ключ ↔ компании |
| `AgentToolPolicy` / presets | FS/shell/network/MCP caps ([08 permissions](../08-agent-providers/permissions-policy.md)) |
| `ModelAllowlist` | Разрешённые model ids ([08 models](../08-agent-providers/models-and-routing.md)) |
| `CompanyAgentRuntimePolicy` | preferred provider, tool preset, quotas, fallback |

## Операции

| Операция | Инвариант |
|----------|-----------|
| `company.create` | Уникальный slug; создаётся учётка Company account |
| `company.suspend` / `activate` | Suspend блокирует логин сотрудников |
| `company.set_subscription` | Дата окончания или `lifetime=true` |
| `company.grant_cabinets` | Только из `CabinetCatalogEntry` |
| `ai_key.*` | Делегировано модулю 02 |
| `agent_policy.*` | Defaults + per-company tool/model/quota |
| `models.sync` / `models.allowlist` | Catalog refresh + allowlists |
| `metrics.companies_overview` | Read-only агрегаты + agent usage |
| `metrics.agent_usage` | Tokens/$, by provider/model |

## Границы доступа

- Admin API: `require_platform_admin`.
- Admin **не** читает содержимое project workspace без отдельного break-glass (аудит) — в v1 мониторинга достаточно метаданных и метрик.
