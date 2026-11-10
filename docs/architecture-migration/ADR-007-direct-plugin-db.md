# ADR-007: Прямой доступ плагина к своей БД (db.direct)

**Статус:** Принято (2026-11-08); уточнено 2026-11-09. **Target design, не as-built:** plugin platform emergency disabled и не подтверждён vertical slice.
**Supersedes where applicable:** ADR-003 (no direct infra access) — **только** в части plugin-контейнеров и только для БД компании.
**Связанные:** ADR-002 (capability gateway), ADR-006 (plugin-модули).

## Контекст

ADR-006 / v2 дизайна выбрали вариант A: реальные таблицы через структурированный манифест `TenantUserDbService._ALLOWED_TYPES`, без выдачи DSN. Это было ошибкой по смыслу задачи: `_ALLOWED_TYPES` запрещает произвольный SQL, JOIN, CTE, `EXPLAIN`, собственные индексы и миграции — то есть воспроизводит ту же «ограниченную декларативность», от которой миграция уходит.

Требование: модуль — полноценное приложение, данные реальные, доступ к БД прямой, к остальной инфраструктуре — через адаптеры.

## Решение

Плагин получает **`TENANT_DB_DSN`** к отдельной БД своей компании `tenant_<company_id>` и роль, которая владеет схемой `module_<module_id>`. **Эта роль сама выполняет DDL собственных миграций.** Платформа делает provisioning БД, схемы и роли, выдаёт DSN и проверяет артефакт, но не применяет plugin DDL доверенной платформенной ролью.

```sql
CREATE ROLE tenant_<company>_plugin_<module>
  LOGIN PASSWORD '...'
  NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS
  CONNECTION LIMIT 10;

GRANT CONNECT ON DATABASE tenant_<company> TO tenant_<company>_plugin_<module>;
CREATE SCHEMA module_<module> AUTHORIZATION tenant_<company>_plugin_<module>;
ALTER ROLE tenant_<company>_plugin_<module> SET search_path = 'module_<module>';
```

### Границы безопасности (перенесены в PostgreSQL, а не в парсер запросов)

| Граница | Механизм |
|---|---|
| Нельзя затереть чужую БД / платформенную `prodavan` | отдельный tenant-Deployment/Service; `GRANT CONNECT` только на свою БД; NetworkPolicy не включает `prodavan` |
| Нельзя создать БД/роль или выдать себе права | `NOCREATEDB NOCREATEROLE NOSUPERUSER` |
| Нельзя выйти из своей схемы | `search_path` + отсутствие `USAGE` на другие схемы |
| DDL фиксируется | `pgaudit` на tenant-кластере → append-only лог |
| Ресурсы | PgBouncer (transaction mode, один пул на кластер), `CONNECTION LIMIT`, `statement_timeout`, `lock_timeout`, `resources.limits` контейнера |
| Секреты | `secret_ref` с ротацией при reinstall/revoke, не plaintext в манифесте |

### Что остаётся через Plugin Gateway

Cache/Redis, Objects/MinIO, Docs/Mongo, Events, Jobs, Pods, MCP, Secrets, Network — по typed контрактам **`/capabilities/v1/*` в Plugin Gateway** с `PluginClaims`. Агентский `:8001` не расширяется. Objects — tenant/prefix-scoped с **scoped presigned по умолчанию**, STS только отдельно проверенная опция. Платформенная БД `prodavan` недоступна плагину **никак**.

### Что меняется в остальных решениях

- **RLS не используется** как граница: при БД-на-компанию фильтр по `tenant_id` бессмыслен, `ENABLE` без `FORCE` не действует на owner, `app.*` GUC пере-set'ится клиентом.
- **Миграции (Liquibase/Flyway/Alembic) применяет сам плагин** при старте контейнера под своей ролью. Платформа проверяет подпись артефакта, привилегии роли и `pgaudit`, но не ревьюит и не исполняет его DDL своей ролью.
- **Статический парсер запрещённых statement убран**: `DROP DATABASE`, `CREATE ROLE`, `COPY ... TO PROGRAM` невозможны по привилегиям.
- **`CREATE DATABASE` на компанию принято** (в v2 отвергалось): при прямом DSN это и есть настоящая граница изоляции. Один кластер, N БД.
- **ADR-003 продолжает действовать для агентов** и для всех не-БД ресурсов плагинов.

### Данные

Это dev-разработка: данные эфемерны. **Бэкапы, перенос данных, snapshot данных, dual-read/dual-write, `migrate-to-plugin`, «N недель наблюдения» не требуются и исключены из дизайна.** Strangler сохраняется только из-за объёма кода `mod_equipment` (~13k строк). Source snapshot/patch в предложенном ADR-009 не относится к snapshot данных.

## Последствия

- Плагин получает полноценный SQL и владение схемой — без переделки в будущем.
- Усложняется инфраструктура: отдельный tenant-Postgres, PgBouncer, `pgaudit`, управление БД/ролями при grant/uninstall.
- Требуется дисциплина: роль создаётся платформой, пароль — `secret_ref`, сеть — NetworkPolicy. Ошибка в `GRANT CONNECT` = утечка, поэтому она остаётся acceptance-критерием. **Все live acceptance сейчас `[ ]`**, см. `docs/migration/plugin-acceptance-map.md`.
- Уходит «ложная защищённость» RLS/GUC-подхода.

## Связанные документы и cutover

- `docs/ARCHITECTURE/plugin-architecture-complete.md` §2, §3, §6.1 — **v4 target snapshot, только вместе с errata**.
- `docs/migration/plugin-cutover-safety.md` — текущий STOP для platform schema removal; plugin-owned migration не равна удалению platform declarative tables.
