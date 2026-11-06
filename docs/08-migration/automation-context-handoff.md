# Automation context handoff: `project_triggers` fence

Статус: migration-stage application contract. Документ и пакет
`prodavan.application.automation_context_handoff` закрывают только границу
контекста/фенса для очереди `project_triggers`. DB, ORM, Alembic, routes и
legacy worker не меняются.

## Boundary

`project_triggers` -- отдельная partition. Она не является alias для
`webhook_deliveries`, outbound delivery или webhook outbox. Проектный trigger
может породить target delivery только если automation явно объявляет такое
действие; сам факт наличия payload или lease этого не делает.

Каждый handoff несёт типизированный parent:

```text
tenant_id -> project_id -> run_id
```

`TaskIdentity`, `OutboxIdentity` и `DeliveryIdentity` повторяют этот parent.
Контракт отвергает смешение parent ids. Delivery является optional projection:
project trigger без outbound side effect не создаёт delivery identity.

## Identity and idempotency

`ProjectTriggerIntent` хранит source `trigger_id`, immutable payload и
partition. `project_trigger_idempotency_key` строится только из стабильной
source identity (tenant, project, trigger); payload и kind дают отдельный
`request_hash`. Ключ передаётся одинаковым task/outbox/delivery identity.
Реализация порта обязана атомарно claim-ить `(tenant, partition, key,
request_hash)` вместе с task/outbox intent.

Повтор с тем же ключом возвращает исходный intent (`duplicate=true` на уровне
claim/repository) и не создаёт новый run, outbox event или delivery. Разный
request hash для того же ключа -- conflict, а не overwrite. Queue kick после
commit является repair и обязан быть idempotent.

Это application contract, не database uniqueness declaration. Будущий adapter
должен обеспечить уникальность и прочитать победителя при concurrent insert;
`SELECT`-then-insert недостаточен.

## Worker generation/fence

`WorkerFence` состоит из partition, worker id и положительного monotonic
`generation`. Claim/complete/retry порт получает весь fence, а не только
worker id. Старый worker после lease takeover не может завершить run: adapter
должен выполнить conditional update по task id + partition + owner +
generation. `owner_id` без generation недостаточен.

Legacy `leased_by`/`lease_until` у `ProjectTriggerRow` не считаются target-safe
fence. До promotion требуется остановить legacy claims, дождаться завершения
или expiry активных legacy leases и выдать target generation.

## Promotion evidence

`PromotionEvidence.promotable` требует все четыре факта:

- legacy claims stopped;
- active legacy leases drained;
- parity zero для согласованного observation window;
- idempotency replay verified.

Evidence scoped to tenant/project/partition и содержит checkpoint id и target
worker fence. Неполный evidence отвергается сервисом; переключение ownership не
выводится из наличия target rows, зеркала или истёкшего legacy lease.

Promotion не означает удаление legacy данных. После ownership change legacy
projection read-only, а rollback является forward routing change через target
path. Нельзя одновременно сделать legacy и target workers eligible для одного
side effect или потерять исходный idempotency key.

## Ports and acceptance contract

Пакет предоставляет только:

- immutable value objects и identity derivation в `contracts.py`;
- `AutomationContextHandoffPort` для atomic record, fenced claim/complete и
  promotion;
- `AutomationContextHandoffService` для сборки intent и policy checks;
- unit contract tests с in-memory fake port.

Acceptance criteria:

1. tenant/project/run parent присутствует и совпадает во всех identity;
2. partition и worker fence всегда `project_triggers`, отдельно от webhook delivery;
3. task, outbox и optional delivery используют один стабильный idempotency key;
4. duplicate replay возвращает исходный task/run без второго side effect;
5. claim completion передаёт generation, stale generation не принимается;
6. failed completion без error отвергается;
7. promotion без полного evidence отвергается;
8. payload не изменяется при retry, а delivery id остаётся стабильным.

SQL adapter, migration/backfill, route facade, worker wiring и formal checks
намеренно отложены до отдельного migration wave. Тесты написаны как acceptance
contracts, но в verification freeze не запускаются.
