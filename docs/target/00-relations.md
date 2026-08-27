# Relations — единый модуль связей

Центральный in-process BC для **связей между сущностями** (не composition FK).  
Карта сущностей: [00-entities.md](00-entities.md) · Ownership: [00-ownership-matrix.md](00-ownership-matrix.md).

## Зачем

Связи (membership, grant, assignment, binding) раньше жили разрозненно в BC-сервисах.  
Relations = **единый контракт**: кто с кем связан, какой вид связи, active/revoked.

## Виды связей (`relation_kind`)

| Kind | Смысл | Примеры |
|------|--------|---------|
| `link` | простая ассоциация | future peers |
| `membership` | субъект в org + role | Employee ↔ Company |
| `assignment` | операторский доступ | Employee ↔ Cabinet |
| `grant` | org entitlement | Company ↔ Cabinet / Module |
| `binding` | привязка ресурса | AI key ↔ Company; Module ↔ Cabinet/Project |
| `ownership` | владение (часто зеркало `owner_*` на entity) | company-owned key/cabinet |

Модель логической строки:

```text
(subject_kind, subject_id, object_kind, object_id, relation_kind, role|mode, status: active|revoked)
```

## Что **не** в Relations

- Composition FK: `Project.cabinet_id`, `Project.owner_employee_id` (entity owns structure)
- Pod / MinIO / Auth Keycloak users
- Content blob bytes (ACL entries могут мигрировать later)

## Write / Read

| Ось | Кто | Как |
|-----|-----|-----|
| **Write** | Owning BC REST → `RelationsCommand` | Sync commit SoT (сейчас существующие таблицы) → after_commit Kafka `relation.granted` / `relation.revoked` / `relation.replaced` |
| **Read (ACL)** | Любой BC → `RelationsQuery` | Sync PG (не ждать Kafka) |
| **Side-effects** | Modules / Projects consumers | KafkaManager → materialize / pause projects — **не** inline из grant_service |

```text
BC_REST → RelationsCommand → PG
                 └─ after commit → Kafka relation.*
KafkaManager → side-effect handlers (Modules, Projects, …)
BC_REST / ACL → RelationsQuery → PG
```

Топики: `prodavan.relation.commands` (опционально для async writers), `prodavan.relation.events` (обязательно для fan-out).

## Изоляция

- BC **не** импортируют чужие grant-таблицы напрямую для ACL — только `RelationsQuery`.
- Side-effects после grant/revoke — через events, не `from application.projects import …` из AiKeys/Cabinets.
- Auth остаётся на `prodavan.auth.*` (отдельный контур).

## Фазы

1. **Facade** над текущими таблицами (`memberships`, `cabinet_*`, bindings).
2. Soft-status унификация (membership revoke как grants).
3. Later: physical consolidate / polymorphic store.

Пакет кода: `application/relations/` (+ domain kinds). Gap: **P-REL-01**.
