# Equipment evidence provenance handoff

**Статус:** проект миграции, exclusive application boundary; persistence, ORM, Alembic и routes не входят в этот артефакт.
**Дата:** 2026-11-01

## Цель

Передать provenance оборудования между ingestion, visibility, matching и procurement так, чтобы ни поиск, ни выбор, ни пересчёт закупки не имели side effects без доказанного evidence. Handoff связывает одну цепочку:

```text
tenant → project → owner partition → supplier → source chain
  → ingestion run → raw artifact → catalog snapshot → offer
  → candidate group → selected offer → procurement
```

`EquipmentEvidenceBundle` в `application/equipment_evidence_handoff/` является immutable transport-neutral contract. Он не читает БД напрямую и не импортирует ORM, SQLAlchemy, Alembic или route-код.

## Partitions and visibility

| Объект | Evidence link | Trusted owner / visibility rule |
|---|---|---|
| tenant/project | `owner.tenant_id`, `owner.project_id` | обязательны на owner, promotion и quarantine; cross-scope отклоняется при создании контракта |
| owner | `owner_id`, `visibility_key` | visibility port подтверждает, что проект имеет право видеть handoff |
| supplier | `supplier_key` | поставщик обязателен в owner, promotion и quarantine partitions, должен присутствовать в `source_chain`; supplier scope проверяет visibility adapter |
| source chain | `source_id`, `run_id`, `artifact_id`, `source_chain`, `provenance_digest` | chain не может быть пустой и обязана содержать supplier/source/run/artifact; digest фиксирует каноническую provenance-политику и передаётся visibility port вместе с chain |
| snapshot | `snapshot_id` и promotion partition | promotion snapshot обязан совпадать с bundle snapshot |
| offer | `offer_id` | offer допускается только при доказанной связи с candidate group |
| candidate | `candidate_group_id` | `MatchingReadPort` доказывает candidate↔offer link; ID сам по себе не является доказательством |
| selected | `selected_offer_id` | optional только для no-selection state; если задан, совпадает с evidenced offer |
| procurement | `procurement_id` | downstream identity сохраняется в том же owner/evidence scope, но не создаётся handoff-сервисом |
| promotion | `EquipmentPromotionPartition` | trusted append разрешён только для `verified` decision; supplier и snapshot входят в ту же partition |
| quarantine | `EquipmentQuarantinePartition` | invalid/invisible evidence идёт только в отдельный quarantine append; supplier входит в partition, quarantine не даёт positive writes |

`visibility_key` не заменяет tenant/project: это дополнительный policy key, а не источник scope. Нельзя выводить owner, tenant или project из `offer_id`, `supplier_key`, строкового префикса или JSON payload.

## Service rules

`EquipmentEvidenceHandoffService.verify()` имеет нулевые append-side-effects и выполняет проверки в fail-closed порядке:

1. owner visibility;
2. source-chain visibility;
3. candidate-group↔offer membership;
4. optional immutable evidence-id reuse check (same evidence ID with a different provenance digest is a conflict).

`handoff()` после verify вызывает ровно один из двух портов: `append_verified` или `append_quarantine`. Quarantine никогда не вызывает matching, selection, procurement или trusted append.

`authorize_matching`, `authorize_selection` и `authorize_procurement` возвращают только verified decision. При любой недоказанной цепочке они выбрасывают `EvidenceHandoffError` до вызова downstream operation. Сам handoff не реализует matching/selection/procurement и не подменяет human approval; эти операции остаются у соответствующих application services.

## Integration boundary

- ingestion передаёт source/run/artifact/snapshot identities;
- `equipment_visibility` предоставляет read-only visibility/provenance checks;
- `equipment_matching` может получить разрешение только после `authorize_matching` и самостоятельно применяет typed command policy/human approval;
- selected/procurement paths используют соответствующие authorize-методы до своих write ports;
- persistence adapter позже реализует append/read ports в одной транзакционной boundary с idempotency по evidence/provenance digest и положительным writer-generation CAS; stale generation даёт zero-row/no-side-effects результат, а конфликт digest остаётся quarantine/conflict и не перезаписывается; он не меняет immutable contract и не объединяет trusted/quarantine partitions;
- routes и DB/ORM wiring добавляются отдельным migration item после reviewed persistence design.

## Acceptance criteria

- cross-tenant/project owner, promotion или quarantine partition не конструируется;
- пустой source chain и отсутствующая supplier/source-chain visibility кварантинируются;
- candidate/offer link без доказательства не запускает matching, selection или procurement;
- verified append не смешивается с quarantine append;
- verify не создаёт rows и не вызывает side-effect port;
- selected/procurement IDs сохраняются как ссылки evidence, но не разрешают автоматический выбор или закупку;
- нет DB/ORM/Alembic/routes в exclusive зоне.

Тесты в `apps/api/tests/unit/test_equipment_evidence_handoff.py` фиксируют эти acceptance checks; согласно migration verification freeze они намеренно не запускаются на этом этапе.
