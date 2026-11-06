# Full identity partition digest

Статус: migration-stage source contract, baseline 2026-11-01. Документ описывает
новую exclusive zone `application/full_identity_partition/`. Все тесты, build,
lint, compile, Alembic и git checks для этой задачи намеренно не выполняются.

## Назначение

`FullIdentityPartitionResolver` принимает уже validated `shared_identity.IdentityEnvelope`
и matching `shared_identity.PromotionPartition`. Он вычисляет один canonical
promotion digest, связанный с полной identity snapshot. Зона не является ORM,
репозиторием, route или authorization decision и не изменяет существующие
`shared_identity` и `promotion_partition_digest` зоны.

## Digest payload

Canonical JSON использует UTF-8, ASCII-safe encoding, sorted keys и compact
separators. В digest входят:

- definition, version и immutable artifact digest;
- tenant, cabinet, project, typed scope и exact plugin instance anchors;
  cabinet/project installation anchors are digest-bound as part of the full envelope;
- subject и session;
- runtime и writer generations;
- lease snapshot (`lease_id`, `lease_owner`, timezone-aware `lease_until`) как
  evidence fencing;
- correlation и trace IDs;
- полная `PromotionPartition` с tenant, definition, version, scope, plugin
  instance, subject и session.

Digest имеет формат `sha256:<64 lowercase hex>`. Если supplied digest есть, он
проверяется и при расхождении операция отклоняется; исходное значение не
переписывается.

## Fail-closed boundaries

Резолвер требует exact object types из `shared_identity` и сравнивает partition
с envelope-derived partition. Decision contract повторно проверяет digest envelope и
совпадение derived partition, поэтому прямое конструирование не обходит boundary.
Mapping boundary принимает только восемь полей
полной partition. Формы с `plugin_id`, `supplier_id`, `owner_id`, отсутствующими
anchors или неизвестными полями отклоняются. Эти legacy/partial shapes не могут
создать promotion identity.

Lease snapshot участвует в digest evidence, но сервис не строит и не возвращает
lease headers. Никакие secrets, credentials, access tokens, prompt contents или
произвольные mutable mappings не входят в payload. Transport propagation остаётся
ответственностью существующих safe identity context boundaries.

## Source acceptance

`apps/api/tests/unit/test_full_identity_partition.py` фиксирует canonical
recomputation, binding каждого identity/fencing поля, exact partition matching,
partial-shape rejection, supplied digest conflict и отсутствие secret/header
propagation. Тесты написаны как migration source contracts и намеренно не
запускаются в verification freeze.

## Unresolved risks

- Старые promotion consumers ещё должны перейти на полный envelope-bound digest;
  эта зона не меняет их API и не выполняет positive writes.
- Проверка lease expiry, CAS, registry ownership и authorization остаётся
  обязанностью downstream transaction boundary.
- Cryptographic transport trust и header extraction требуют отдельного boundary
  review; digest headers сами по себе не являются authority.

Verification freeze: tests, build, lint, formatting, compile, Alembic, database,
environment and git checks were intentionally not run.
