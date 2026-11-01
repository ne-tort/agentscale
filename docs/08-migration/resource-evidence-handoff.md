# Resource evidence handoff

Статус: migration design artifact. Этот boundary закрывает handoff между ресурсным resolver/materialization и будущим runtime apply. Он не добавляет ORM, SQLAlchemy-модели, Alembic revision, БД или HTTP routes.

## 1. Решение

`application/resource_evidence_handoff/` является единственной application-зоной, которая допускает resource apply после проверки runtime evidence. Пакет не владеет ресурсами, grants или runtime state и не читает persistence напрямую. Он принимает server-observed evidence через порты и делегирует ровно один fenced CAS apply.

Границы ownership:

| Объект | Владелец | Handoff обязан проверить |
|---|---|---|
| Resource identity/payload/version | resource/materialization source | resource id и provenance resource revision |
| Owner and grant | resource grant owner | exact grant id, owner scope и grant generation |
| Promotion partition | migration/runtime owner | tenant, project, partition и owner identity |
| Runtime revision | runtime revision source | exact revision id, project и observed generation |
| Applied generation | apply/runtime port | CAS against expected generation |
| Provenance | evidence source | evidence id, source digest и resource revision |

Наличие resource row, binding или payload само по себе не является разрешением на apply. Нет runtime revision или grant/provenance evidence — apply запрещён.

## 2. Канонические контракты

`ResourceEvidenceHandoff` содержит:

- `project_id`;
- `PromotionPartition(tenant_id, project_id, partition_id, owner_id)`;
- `RuntimeRevisionEvidence(project_id, revision_id, generation, evidence_id, provenance_digest)`;
- `ResourceGrantEvidence(resource_id, owner, grant_id, grant_generation)`;
- непустой набор `ResourceProvenance` с resource revision/source digest/evidence id;
- `expected_generation` и operation digest.

Контракты immutable (`dataclass(frozen=True, slots=True)`). Handoff не создаётся с пустыми identity или несовпадающим project. Mapping provenance фиксируется immutable snapshot-ом.

## 3. Fail-closed apply

`ResourceEvidenceHandoffService.apply_handoff` выполняет проверки до вызова `ResourceApplyPort`:

1. Получает server-side runtime evidence для project. Client-supplied revision не принимается как proof.
2. Сверяет server-observed `project_id`, точный `revision_id` и `evidence_id` с handoff, затем проверяет exact generation и непустой provenance digest.
3. Получает server-side grant evidence для resource.
4. Сравнивает grant identity/owner/generation с handoff и partition; owner обязан иметь `ScopeKind.PROJECT` и exact `project_id` partition.
5. Проверяет непустой provenance и соответствие каждого provenance resource.
6. При любой ошибке возвращает `ResourceApplyResult(applied=False, failures=...)`; apply port не вызывается.
7. Только полный набор evidence передаётся в один `compare_and_swap_apply`.
8. Даже успешный ответ отклоняется, если CAS вернул не тот generation.

Никакого partial apply: операции не применяются по одному ресурсу до завершения handoff gate. Ошибки классифицируются typed-кодами: `missing_runtime_revision`, `missing_evidence`, `revision_mismatch`, `generation_stale`, `owner_mismatch`, `grant_mismatch`, `partition_mismatch`, `provenance_missing`, `cas_conflict`.

## 4. Owner/grant и promotion partition

Partition является явной ownership boundary, а не вычисляется из resource metadata. Один partition должен иметь одного eligible owner и один apply writer. Resource owner scope обязан указывать на тот же project partition; cross-project resource evidence отклоняется.

Grant identity (`grant_id`) и grant generation не выводятся из resource version и не заменяются «текущим grant». Это отдельное доказательство. Любая смена owner/grant должна породить новый evidence snapshot и generation; старый handoff нельзя переиспользовать.

## 5. Runtime revision и generation

Runtime revision (`revision_id`) — точная версия runtime snapshot, на который рассчитан resource handoff. Generation — fencing/CAS значение, а не порядковый номер resource version. Apply разрешён только при равенстве:

```text
runtime.project_id == handoff.project_id
runtime.revision_id == handoff.runtime.revision_id
runtime.evidence_id == handoff.runtime.evidence_id
runtime.generation == expected_generation
grant.grant_generation == expected_generation
```

Stale runtime, stale grant и unknown generation являются deny, а не поводом повторить apply с автоматически выбранным latest generation. Повторная попытка должна сначала получить новый server-side evidence snapshot.

## 6. Provenance

Каждый resource должен быть связан с `resource_revision`, `source_kind`, `source_digest` и `evidence_id`. Runtime envelope дополнительно хранит `provenance_digest`. Эти значения позволяют связать applied state с конкретным resource source и runtime snapshot без копирования payload в handoff boundary.

Provenance не является authorization grant: она подтверждает происхождение, но не заменяет owner/grant check. Grant не является provenance: наличие разрешения без source evidence также deny.

## 7. Порты и реализация

- `ResourceEvidencePort` — read-only server evidence для runtime и grant.
- `ResourceApplyPort` — будущий persistence/runtime adapter с атомарным `compare_and_swap_apply`.
- `ResourceEvidenceHandoffService` — orchestration и fail-closed gate.

Порты не требуют DB/ORM/Alembic и не подразумевают конкретный storage. Никаких routes или background workers в этом migration slice нет.

## 8. Acceptance criteria

Готовым считается handoff, когда:

- resource apply без runtime revision невозможен;
- resource apply без grant или provenance evidence невозможен;
- owner/grant/project partition mismatch закрывается deny;
- stale runtime/grant generation не доходит до apply port;
- apply выполняется одним CAS-вызовом с expected generation;
- unexpected CAS generation превращается в `cas_conflict` и deny;
- provenance сохраняет exact resource revision/source digest/evidence id;
- boundary не добавляет DB/ORM/Alembic/routes и не становится вторым resource materialization adapter.

## 9. Rollback/failure handling

При неизвестном результате CAS нельзя выпускать компенсирующий apply и нельзя повышать generation локально. Partition остаётся deny до получения свежего evidence и повторной попытки с новым operation digest. Ошибочные или неполные evidence snapshots сохраняются/классифицируются владельцем evidence adapter; этот boundary не делает destructive cleanup и не скрывает конфликт.
