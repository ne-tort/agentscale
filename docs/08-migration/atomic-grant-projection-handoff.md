# Atomic grant projection handoff

Статус: migration-stage application contract, 2026-11-01.

Документ закрывает application boundary для grant projection между M04 owner/scope
foundation, M05 plugin-instance/runtime fencing и M06 canonical grant decision. Он
не добавляет DB/ORM/Alembic/routes и не является разрешением на shared-environment
DDL или cutover. Formal tests/build/lint/compile/git checks намеренно не выполняются
из-за migration verification freeze.

## Решение

`apps/api/src/prodavan/application/atomic_grant_projection/` является exclusive
application boundary для перехода grant decision в canonical projection. Новый
caller создаёт полный `AtomicGrantProjectionCommand` и вызывает только
`AtomicGrantProjectionRepository.execute(command)`. Repository владеет одной
транзакцией и внутри неё атомарно обрабатывает:

1. server-resolved M04 context (tenant, typed scope, definition/version, artifact и
   owner evidence digest);
2. server-resolved M05 context (exact plugin instance, subject/session,
   runtime/writer generations, lease и artifact);
3. canonical grant decision и его capability/operation/resource/domain context;
4. bounded promotion partition/evidence;
5. canonical grant or deny outcome, quarantine evidence, mapping/projection and
   immutable audit evidence.

Никакого optional split transition нет. В порте отсутствуют `add`, `update`,
`delete`, `project`, `quarantine` и `promote`; положительный grant, deny,
quarantine, legacy projection и audit не могут быть записаны разными application
вызовами. Existing decision-only adapters may resolve evidence, but they do not
replace this repository-owned command.

## Обязательный command context

`M04GrantProjectionContext` должен содержать `tenant_id`, typed `scope_kind` и
`scope_id`, `app_definition_id`, `app_id`, `app_version_id`, `manifest_version`,
immutable `artifact_digest` и owner evidence digest. `M05GrantProjectionContext`
должен содержать exact `plugin_instance_id`, `subject_id`, `session_id`, positive
runtime/writer generations, lease identity/owner и artifact digest.

Command construction fails closed when:

- M04 или M05 context отсутствует или имеет пустую identity;
- M04/M05 artifact digests, scope, instance или partition identity differ;
- canonical decision says allow without `reviewed_final` promotion evidence;
- quarantine decision has no redacted `QuarantineRecord`;
- quarantine/deny carries a positive canonical allow or final promotion evidence;
- partition is not the same bounded tenant + app definition + narrowed runtime
  identity represented by M04/M05.

`PromotionRecord` is evidence only. It does not grant authority by itself:
canonical allow is required as well. `QuarantineRecord` is never an authorization
input and cannot enable positive writes.

## Repository transaction ordering

The repository implementation (future persistence adapter) must perform the
following in one transaction, with no caller-visible intermediate commit:

1. establish transaction-local authenticated tenant/scope/subject context;
2. fence the partition and compare the expected writer/runtime generations;
3. resolve and lock the exact M04 owner/definition/version and M05 instance;
4. compare command identity, artifact, scope, lease and canonical decision digests;
5. apply one idempotent canonical outcome under active uniqueness/CAS rules;
6. append mapping, quarantine (when applicable), compatibility projection and
   immutable audit evidence in the same transaction;
7. commit only when every required context and evidence check passes.

On any failure, the transaction must abort or persist the explicitly requested
closed quarantine outcome atomically. It must never commit a grant while audit,
quarantine, mapping, fencing, or promotion evidence is missing. No fallback to
legacy JSONB/requested capabilities, wildcard broadening, another instance, or
another policy owner is allowed.

## Handoff boundaries

- **M04 owner/scope:** supplies typed active ownership and definition/version proof;
  unresolved or ambiguous parent rows are quarantine/deny.
- **M05 runtime:** supplies exact instance and current subject/session/generation/
  lease proof; stale generations are deny/quarantine and never fallback.
- **M06 canonical decision:** supplies the canonical allow/deny/quarantine result;
  requested manifest capabilities are not authorization.
- **Promotion:** supplies bounded partition evidence and final-delta/fence proof;
  missing gate evidence blocks allow.
- **Repository adapter:** is the only future owner of DB transaction, ORM mapping,
  compatibility projection, audit and quarantine persistence.

The implementation intentionally stops at contracts, ports, service and acceptance
contracts. No SQL, SQLAlchemy model, Alembic revision, route, or persistence
adapter belongs in this handoff.
