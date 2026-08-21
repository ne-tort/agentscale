# Platform Admin — domain

## Ответственность

Platform Admin:

1. Создаёт и сопровождает **Company**.
2. Управляет сущностями **AI Provider Key** (см. модуль 02).
3. Назначает компании **allowlist кабинетов** (какие cabinet modules доступны).
4. Видит **агрегированный мониторинг** по всем компаниям.
5. Не работает внутри кабинета как Employee (нет смешения контуров).

## Сущности (платформенный контур)

| Сущность | Описание |
|----------|----------|
| `Company` | Организация: slug, name, subscription, status |
| `CompanySubscription` | `ends_at` nullable = бессрочно; `plan` опционально |
| `CabinetCatalogEntry` | Зарегистрированный cabinet module (`profile_id`) |
| `CompanyCabinetGrant` | M:N: компания ↔ разрешённые кабинеты |
| `AiProviderKey` | См. модуль 02 |
| `CompanyAiKeyBinding` | M:N: ключ ↔ компании |

## Операции

| Операция | Инвариант |
|----------|-----------|
| `company.create` | Уникальный slug; создаётся учётка Company account |
| `company.suspend` / `activate` | Suspend блокирует логин сотрудников |
| `company.set_subscription` | Дата окончания или `lifetime=true` |
| `company.grant_cabinets` | Только из `CabinetCatalogEntry` |
| `ai_key.*` | Делегировано модулю 02 |
| `metrics.companies_overview` | Read-only агрегаты |

## Границы доступа

- Admin API: `require_platform_admin`.
- Admin **не** читает содержимое project workspace без отдельного break-glass (аудит) — в v1 мониторинга достаточно метаданных и метрик.
