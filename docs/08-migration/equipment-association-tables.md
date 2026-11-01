# `equipment_v2`: relational association tables for JSONB references

**Статус:** проект миграции, не изменение текущей схемы  
**Дата:** 2026-11-01  
**Область:** только дизайн association tables и переход с JSONB-ссылок; ORM, API и существующие документы этим артефактом не меняются.

## 1. Цель и границы

В текущем `equipment_v2` две коллекции являются ссылками на другие сущности:

- `equipment_v2_candidate_groups.offer_ids` — список офферов, входящих в группу кандидатов;
- `equipment_v2_tenders.candidate_group_ids` — список групп, включённых в тендер.

`equipment_v2_candidate_groups.selected_offer_id` уже является скалярной FK-ссылкой на `equipment_v2_catalog_offers.id`, но её scope пока нельзя полностью защитить на уровне БД: текущая схема не несёт единый `(tenant_id, project_id)` на всех участниках связи.

Следующие JSONB-поля **не являются association references сами по себе** и на первом этапе остаются JSONB payload:

| Поле | Смысл | Решение |
|---|---|---|
| `catalog_snapshots.metadata_json` | provenance/метаданные снимка | оставить JSONB; отдельные часто фильтруемые атрибуты нормализовать позже по отдельному решению |
| `catalog_offers.attributes` | нормализованные характеристики предложения | оставить JSONB; не извлекать произвольные ключи в связи |
| `request_lines.specification` | требования к позиции | оставить JSONB; это value object, не ссылка |

Если внутри этих payload позднее появятся стабильные IDs внешних сущностей, их нужно добавлять отдельными typed columns/association tables, а не расширять неформальный массив ссылок.

Цели перехода:

1. FK и tenant/project scope проверяются PostgreSQL, а не только валидаторами;
2. порядок исходных массивов сохраняется явной позицией;
3. дубликаты и cross-tenant/cross-project ссылки невозможны;
4. backfill идемпотентен и может быть проверен до cutover;
5. старый JSONB остаётся обратимым источником до завершения окна rollback.

## 2. Обязательная scope-модель

### 2.1 Текущее ограничение

Сейчас `tenant_id` есть у ingestion run, request и tender, но отсутствует у snapshot, offer, request line и candidate group. `project_id` в таблицах `equipment_v2` отсутствует. Поэтому простая FK `association.offer_id -> catalog_offers.id` проверяет существование строки, но не принадлежность тому же tenant/project.

До включения строгих association-FK требуется отдельная подготовительная миграция схемы:

- ввести канонический `tenant_id` на каждой scoped equipment row;
- ввести `project_id` на operational rows (`requests`, `request_lines`, `candidate_groups`, `tenders` и association rows);
- для catalog ingestion определить явную политику: catalog source/snapshot/offer являются tenant-scoped и `project_id IS NULL` (shared catalog), либо копируются/привязываются к проекту. Нельзя смешивать эти варианты неявно;
- заполнить scope сверху вниз: `request -> request_line -> candidate_group`, `request -> tender`, `ingestion_run -> snapshot -> offer`;
- добавить composite unique keys, необходимые для scoped FKs, например `UNIQUE (id, tenant_id, project_id)` (для shared catalog — `(id, tenant_id)`);
- запретить `NULL` scope после успешной проверки backfill.

Рекомендуемый MVP: request/matching/tender rows — `(tenant_id, project_id)`; catalog snapshots/offers — `(tenant_id)` и `project_id = NULL`, поскольку один каталог может быть доступен нескольким проектам. Если бизнес потребует project-local catalog, это отдельная модель доступа, а не молчаливое заполнение `project_id` в association row.

Пока project anchor отсутствует в `equipment_v2_requests` и не протянут до request lines, candidate groups и tenders, association tables с `project_id NOT NULL` нельзя считать готовыми к cutover: сначала нужен nullable anchor/backfill, проверка соответствия проектов и отдельная ревизия, которая запрещает `NULL`. Это соответствует persistence-target-map: project-scoped cutover выполняется после backfill, а не угадыванием проекта при загрузке association rows.

### 2.2 Scope invariants

- `candidate_group` и его `request_line` имеют одинаковые `tenant_id, project_id`.
- `tender` и его `request` имеют одинаковый `tenant_id`; tender project scope совпадает с request project scope.
- `candidate_group_offer` имеет scope candidate group; offer должен быть доступен тому же tenant. По текущей карте persistence tenant оффера наследуется через `offer.snapshot -> ingestion_run`, поэтому это не доказывается FK только на `offer_id`: нужна deferred constraint trigger или сервисная транзакция, проверяющая цепочку владельца и shared-catalog policy. При рекомендуемой shared-catalog политике `project_id` оффера остаётся `NULL`, поэтому association row не должна требовать project equality с offer; достаточно tenant ownership плюс проверка доступности каталога для project в policy/service layer. Если каталог станет project-local, это отдельная scope-миграция до включения соответствующего FK.
- Source provenance не выводится из association row: offer должен проходить цепочку `offer.snapshot_id -> snapshot.ingestion_run_id`, а snapshot и ingestion run должны иметь согласованные canonical `source_id` и tenant ownership. Один `offer_id` или копируемый `source_id` не доказывает принадлежность; несоответствие source/tenant — backfill defect, блокирующий cutover.
- `tender_candidate_group` имеет scope tender; group имеет тот же tenant и project **и ту же request**. Scope-FK по `(tenant_id, project_id)` недостаточно: равенство `tender.request_id = request_line.request_id` должно проверяться отдельным trigger/service invariant. Также проверяется tenant request (`tender.request_id -> request.tenant_id`), поскольку текущая ORM-модель не защищает это отдельной FK.
- При смене tenant/project у родителя запись блокируется, либо операция каскадно и транзакционно перепривязывает все дочерние rows. Предпочтительно запретить изменение scope после создания.

## 3. Целевые association tables

### 3.1 `equipment_v2_candidate_group_offers`

Замена `candidate_groups.offer_ids`.

```sql
CREATE TABLE equipment_v2_candidate_group_offers (
    tenant_id             varchar(64) NOT NULL,
    project_id            varchar(80) NOT NULL,
    candidate_group_id    varchar(40) NOT NULL,
    offer_id              varchar(40) NOT NULL,
    ordinal               integer NOT NULL,
    created_at            timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (candidate_group_id, offer_id),
    CONSTRAINT uq_ecg_offer_ordinal
        UNIQUE (candidate_group_id, ordinal)
        DEFERRABLE INITIALLY DEFERRED,
    CONSTRAINT ck_ecg_offer_ordinal_positive
        CHECK (ordinal > 0),
    CONSTRAINT fk_ecg_offer_group_scope
        FOREIGN KEY (candidate_group_id, tenant_id, project_id)
        REFERENCES equipment_v2_candidate_groups (id, tenant_id, project_id)
        ON DELETE CASCADE,
    -- The current ORM has no tenant_id on snapshots/offers; this FK is
    -- illustrative only and becomes legal after an explicit tenant key or
    -- ownership relation is added to the catalog chain.
    CONSTRAINT fk_ecg_offer_offer_scope
        FOREIGN KEY (offer_id, tenant_id)
        REFERENCES equipment_v2_catalog_offers (id, tenant_id)
        ON DELETE RESTRICT
);

CREATE INDEX ix_ecg_offer_by_offer
    ON equipment_v2_candidate_group_offers (tenant_id, offer_id);
```

Notes:

- `PRIMARY KEY (candidate_group_id, offer_id)` removes duplicate IDs while preserving distinct groups using the same offer.
- `ordinal` preserves JSON array order. It is not a business rank; rank/best semantics remain derived fields.
- `ON DELETE RESTRICT` on offer prevents deleting an offer that is still referenced by a candidate group. Because the current ORM deletes offers through `catalog_snapshot ON DELETE CASCADE`, deleting a referenced snapshot is also blocked transitively: PostgreSQL atomically aborts the whole `DELETE` statement/transaction, with no partial deletion and no broken associations. The migration workflow must first detach associations or retain/mark the snapshot and offer stale. If catalog retention requires deletion, make that an explicit domain operation rather than hiding a source outage.
- Do not add `selected` to this table. Selection is already modeled by `candidate_groups.selected_offer_id`; a database check/trigger or application transaction must ensure the selected offer is present in this association.

A stricter design can make the selected FK scoped as `(selected_offer_id, tenant_id)` and add a deferred constraint/trigger that checks membership in this table. PostgreSQL cannot express “FK target must be one of this parent’s child rows” with a plain FK alone; the invariant must be enforced by a deferred constraint trigger or by a single transactional command path. The command path must load the candidate group, request-line/request ownership and offer visibility, then update membership and `selected_offer_id` atomically. It must preserve the domain rule that selection is a deterministic-service operation requiring human approval; agent-authored candidate-group intent cannot write the authoritative selection.

### 3.2 `equipment_v2_tender_candidate_groups`

Замена `tenders.candidate_group_ids`. Эта таблица не может сама по себе выразить, что группа относится к той же `request`, что и тендер: текущая ORM-модель связывает tender с request, а candidate group — с request line. Поэтому перед включением FK association должен существовать deferred trigger или сервисная транзакция, проверяющая цепочку `tender.request_id -> request_line.request_id` для каждой группы.

```sql
CREATE TABLE equipment_v2_tender_candidate_groups (
    tenant_id             varchar(64) NOT NULL,
    project_id            varchar(80) NOT NULL,
    tender_id             varchar(40) NOT NULL,
    candidate_group_id    varchar(40) NOT NULL,
    ordinal               integer NOT NULL,
    created_at            timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tender_id, candidate_group_id),
    CONSTRAINT uq_etender_group_ordinal
        UNIQUE (tender_id, ordinal)
        DEFERRABLE INITIALLY DEFERRED,
    CONSTRAINT ck_etender_group_ordinal_positive
        CHECK (ordinal > 0),
    CONSTRAINT fk_etender_group_tender_scope
        FOREIGN KEY (tender_id, tenant_id, project_id)
        REFERENCES equipment_v2_tenders (id, tenant_id, project_id)
        ON DELETE CASCADE,
    CONSTRAINT fk_etender_group_group_scope
        FOREIGN KEY (candidate_group_id, tenant_id, project_id)
        REFERENCES equipment_v2_candidate_groups (id, tenant_id, project_id)
        ON DELETE RESTRICT
    -- Same-request membership is enforced by the trigger/service invariant
    -- described above; a scope FK alone cannot encode that relationship.
);

CREATE INDEX ix_etender_group_by_group
    ON equipment_v2_tender_candidate_groups (tenant_id, candidate_group_id);
```

`ON DELETE RESTRICT` prevents a candidate group from disappearing while it is part of a tender. Tender deletion removes only its association rows. Reordering is an update of `ordinal`; because ordinal uniqueness is deferred, a reorder must run in one transaction and can temporarily exchange ordinal values without a transient uniqueness failure. The same transaction must preserve the pair primary key and finish with a contiguous, positive ordinal sequence.

### 3.3 Existing scalar references

`candidate_groups.selected_offer_id` is not duplicated as a second selection association. Keep it as the single explicit selection value, but strengthen it as follows:

- retain FK to `catalog_offers` with `ON DELETE SET NULL` only if deleting an offer is an accepted semantic loss; otherwise use `RESTRICT` and represent disappearance as `status=stale`;
- add a scoped composite FK after scope columns exist;
- enforce membership in `candidate_group_offers` in the same transaction, preferably with a deferred constraint trigger;
- preserve `NULL` as “no human selection”, not as an implicit first/lowest offer.

Other scalar IDs found in `equipment_v2` (`request_line_id`, `request_id`, `ingestion_run_id`, `snapshot_id`) already have relational columns/FKs and should not be copied into JSON association tables.

## 4. Uniqueness and referential rules

| Rule | Database mechanism | Reason |
|---|---|---|
| One offer appears once per group | PK `(candidate_group_id, offer_id)` | idempotent writes and no duplicate UI rows |
| One group appears once per tender | PK `(tender_id, candidate_group_id)` | idempotent tender updates |
| Array order is stable | unique `(parent_id, ordinal)` + `ordinal > 0` | round-trip compatibility and deterministic exports |
| IDs are tenant/project-safe | composite FKs including scope | blocks cross-tenant links |
| Selected offer belongs to group | deferred trigger/transactional service check | relational FK cannot encode set membership |
| Group in tender belongs to same request/project | scoped FK + parent invariant | avoids mixing unrelated work contexts |
| No accidental catalog deletion | `RESTRICT` on association offer/group targets | stale is safer than silent history loss |

All write paths must normalize input before insert: accept only string IDs, trim/validate format, reject `NULL`, preserve first occurrence order, and reject duplicates rather than silently changing user intent. For an import-only compatibility path, duplicates may be counted and reported, but the canonical association table must contain one row per pair. Reordering is a transactional update of ordinals (with deferred uniqueness), not a delete/reinsert shortcut; a failed reorder must leave the prior complete ordering intact.

## 5. JSONB backfill

### 5.1 Preconditions and audit snapshot

Run the backfill as a versioned migration/job in the normal Alembic delivery path, never manually against a shared environment. Before inserting associations:

1. freeze writes to the affected `candidate_groups` and `tenders`, or use a dual-write gate with a consistent read timestamp;
2. record source row counts, JSONB null/type violations, duplicate IDs, duplicate ordinals, missing target IDs, source-chain mismatches, cross-scope IDs, same-request violations and selected-offer membership violations in a migration audit table/log;
3. resolve canonical tenant, project, request and source ownership keys first; do not infer tenant, project or source from an ID prefix or from a copied JSON field;
4. take a recoverable snapshot/backup according to the platform migration runbook.

### 5.2 Candidate group offers

For each candidate group, expand `offer_ids` with ordinality. The conceptual PostgreSQL operation is:

```sql
INSERT INTO equipment_v2_candidate_group_offers
    (tenant_id, project_id, candidate_group_id, offer_id, ordinal)
SELECT
    g.tenant_id,
    g.project_id,
    g.id,
    x.value #>> '{}',
    x.ordinal
FROM equipment_v2_candidate_groups AS g
CROSS JOIN LATERAL jsonb_array_elements(
    CASE
        WHEN jsonb_typeof(g.offer_ids) = 'array' THEN g.offer_ids
        ELSE '[]'::jsonb
    END
) WITH ORDINALITY AS x(value, ordinal)
WHERE jsonb_typeof(x.value) = 'string'
-- The target expression is x.value #>> '{}'; malformed elements are audited.
;
```

The real migration must not silently coerce malformed non-array values or non-string array elements to empty/text IDs. The `CASE` above is only shorthand for the intended expansion; malformed rows go to an audit/quarantine result and block cutover unless explicitly accepted. Before insertion, validate every offer exists, belongs to the same tenant, and is eligible under the chosen shared-catalog policy. Do not use `ON CONFLICT DO NOTHING` as the idempotency mechanism before validation: it can hide duplicate source IDs or an ordinal conflict. First classify duplicates/ordinal collisions; after a clean validated run, reruns must use a source-scoped idempotency key or prove the exact pair-and-ordinal projection already exists.

### 5.3 Tender candidate groups

Use the same pattern with `equipment_v2_tenders` as the source, expanding `candidate_group_ids` with ordinality. Apply the same strict string-element validation and duplicate/ordinal audit before insert. Validate group tenant/project and request compatibility before insert. A missing group is not an empty list: it is a referential defect requiring remediation or an explicit tombstone policy.

### 5.4 Reconciliation queries/checks

The migration acceptance gate must compare normalized multisets and order, not JSON byte equality:

- count of valid JSON IDs = count of association rows per parent;
- ordered `array_agg(offer_id ORDER BY ordinal)` equals normalized source array;
- same check for tender groups;
- no duplicate pair, no duplicate ordinal, no orphan association;
- every non-null `selected_offer_id` has a matching group-offer row;
- no cross-tenant/project association;
- every source defect is classified as fixed, quarantined, or explicitly accepted;
- rerunning the backfill produces zero additional rows and no ordinal drift.

Do not remove or rewrite the JSONB arrays during backfill. They are the rollback source until after cutover and the retention window.

## 6. Dual-write, read cutover, and deletion of JSONB

Use an expand/contract sequence:

1. **Expand:** deploy association tables, indexes, scope columns and audit instrumentation. Existing JSONB remains authoritative.
2. **Backfill:** populate associations in batches ordered by parent ID. Keep batches short, commit independently, and use `ON CONFLICT` only after duplicate/conflict reporting is available.
3. **Dual-write:** for one compatibility window, every create/update writes both representations in one transaction. The service owns canonical ordering; JSONB is generated from the association rows, not independently merged.
4. **Shadow-read:** read both forms, normalize them, and emit a mismatch metric/audit record. Do not expose whichever value happens to be read first.
5. **Read cutover:** after mismatch and orphan counters remain zero for the agreed observation window, read associations. Keep JSONB populated as a compatibility mirror for one release/rollback window.
6. **Write cutover:** reject direct JSONB mutation in new code paths; compatibility API requests are translated to association operations and regenerate the mirror.
7. **Contract:** only after rollback expiry, archive the JSONB values or drop the columns in a separate reviewed migration. Dropping columns is not part of the initial cutover.

The cutover must be tenant/project-scoped and resumable. A single bad tenant must not cause an unsafe global “best effort” conversion.

## 7. Rollback

Rollback is a feature/configuration reversal, not an ad hoc SQL edit:

- before read cutover, disable dual-write and continue reading JSONB; association tables may remain as inert expanded schema;
- after read cutover, switch the read feature flag back to JSONB only if the mirror has been kept current and the mismatch audit is clean;
- if an association write succeeded but mirror generation failed, fail the transaction and retry; never accept a partially committed pair;
- if a target offer/group was deleted or scope changed, rollback cannot recreate it from an association table alone. Keep the original JSONB and normal database backup until the retention window ends;
- never downgrade by deleting association rows while JSONB is still the rollback source;
- after contract/drop, rollback requires restoring the archived JSONB/backup into new compatibility columns and replaying associations, so the drop must be a separately approved irreversible step.

A rollback drill should prove: restore one parent from JSONB, regenerate associations with identical order, preserve `selected_offer_id`, and leave unrelated tenants/projects untouched.

## 8. Operational acceptance criteria

Migration is ready for implementation only when all are true:

- every association target has a documented owner and FK target;
- tenant and project scope is present and enforced, including shared-catalog policy;
- duplicate IDs and duplicate ordinals are rejected deterministically;
- backfill is idempotent, resumable and produces an auditable defect report;
- ordered normalized source and association projections match;
- selected offers are members of their group, including concurrent update handling;
- tender membership cannot cross request/project scope;
- stale offers remain referentially usable; deletion is not used to hide source outages;
- dual-read mismatch metrics are zero for the observation window;
- rollback can switch reads without losing the JSONB mirror;
- the final JSONB column removal is a later, separately reviewed contract migration.

This document is a design artifact only. It intentionally does not add ORM models, Alembic revisions, API behavior, tests, or changes to existing equipment documentation.
