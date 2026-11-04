# M04 → M04E → M05 → M06 → cutover: сквозные швы и проводка (ревиз)

Task: **MIG-REVIEW-SEAMS-01**. Статус: **re Review**, кода не менялось.
Authorization: migration-stage protocol, verification freeze (`AGENTS.md`).
Приёмка выполнена статическим чтением кода/контрактов и `grep`/`glob`; тесты, сборки, компиляция,
линтеры, алембик и git-проверки намеренно не запускались.

Артефакт-компаньон (исполняемая часть приёмки):
[`apps/api/tests/migration/integration_seams/test_seam_contract.py`](../../apps/api/tests/migration/integration_seams/test_seam_contract.py).

Обозначения: **app** = `apps/api/src/prodavan/application/migration/**`,
**infra** = `apps/api/src/prodavan/infrastructure/persistence/**`.
Все ссылки — `файл:строка` на момент ревизии.

---

## 1. Главный вывод

Цепочка M04 → M04E → M05 → M06 → cutover **нигде не собрана как процесс**. Это набор
library-пакетов с корректными fail-closed контрактами и почти полным набором persistence-адаптеров,
но **без единого composition root**: ни один сервис цепочки не создаётся в `src/prodavan`, а
`core/wiring.py` не упоминает migration вообще.

Проверено построчно: конструкторы `ScopeRegistryApplicationAdapter(`, `DefinitionVersionEvidenceBackfill(`,
`ScopeMigrationCoordinator(`, `InstanceImportService(`, `CutoverCommandService(`, `build_shadow_gateway(`
существуют **только в `tests/`**. Следствие: сегодня cutover невозможен не из-за одного дефекта, а
из-за обрыва передачи evidence в четырёх местах (см. §4), и любой из них по отдельности уже блокирует
промоцию партиции.

---

## 2. Карта швов (фактическая)

### 2.1 Сводная таблица

| Шов | Контракт объявлен | Реализация | Кто вызывает | Совместимость |
|---|---|---|---|---|
| **M04 → M04E** (active typed scope + owner proof) | `ResolveScopePort.resolve_active` — app/m04_scope/scope_migration.py:640; `ScopeResolutionEvidence` :629. Потребители: `ActiveTypedScopePort` — app/m04e_definition_version/repository.py:89, `DefinitionVersionEvidencePort` :97, `UpstreamGatePort` — backfill.py:265, `CandidateProofPort` — backfill.py:271, `EvidenceStore` — backfill.py:280 | app: `ScopeRegistryApplicationAdapter` — app/scope_registry_adapter/adapter.py:78. infra: `SqlAlchemyTrustedScopeTransactionPort(TrustedScopeTransactionPort)` — infra/migration_scope/adapter.py:101; `M04EDefinitionVersionPersistenceAdapter` (`prove_active_scope` :376, `prove_and_record` :463, `record_denial` :541) — infra/migration_definition_version/adapter.py:142; `M04EArtifactContentAdapter` — infra/.../content_adapter.py:1 | `ScopeMigrationCoordinator` — app/m04_scope/scope_migration.py:701; `DefinitionVersionEvidenceBackfill.run_page` — backfill.py:351. **Оба инстанциируются только тестами** | ❌ тип возврата app-адаптера ≠ контракт; ❌ `EvidenceStore` без реализации |
| **M04E → M05** (handoff marker) | Producer: `M05HandoffMarker` — app/m04e/backfill.py:1056, `M05HandoffPort.append_marker` :1112, `persist_m05_handoff` :1118. Consumer: `M05UpstreamEvidence.ready_for_import` — app/m05/runtime_service.py:173, `marker_ready` :199–214 | Producer: **нет реализации `M05HandoffPort`**. Consumer: `M05UpstreamEvidence` **не конструируется в src** (только tests/m05/test_runtime_acceptance.py:177) | `persist_m05_handoff` вызывается только из тестов | ⚠️ поля marker'а совпадают (✔), но producer-порт пуст и `checkpoint_digest` у M04E-чекпоинта отсутствует |
| **M05 → M06** (promotion/handoff) | Producer: `M05PromotionService.record_m06_handoff` — runtime_service.py:1636, `PromotionRecord` (task_id `MIG-M05-02`) :1396/1621, `M05PromotionRecordPort.append_immutable` :1425. Consumer: `PartitionHandoffReadPort.load_partition` — app/m06_grants_gateway/handoff.py:120, `AcceptedPartitionEvidence` :47, `DependencyGates` — adapter.py:103 | Producer: **нет реализации `M05PromotionRecordPort`**. Consumer: **нет реализации `PartitionHandoffReadPort`** | `build_m06_handoff_record` — только тесты | ❌ M05 record не может дать `accepted_sources`/`m05_instance_digest`/`writer_epoch`, которые требует `AcceptedPartitionEvidence` |
| **M06 → cutover** | `CutoverCommandService` — app/m06_cutover/cutover.py:575; порты: `CutoverRepository` :557, `GateVerifier` :331, `LegacyOwnerVerifier` :335, `RollbackVerifier` :339, `CanonicalRepairVerifier` :343; evidence: `GateEvidence` :154, `GateLineageEvidence` :76 | **ни одной реализации** ни в app, ни в infra | `CutoverCommandService(` — только tests/m06_cutover/test_acceptance_contract.py:75 | ❌ нет mapper'а `ParityRunReport` → `GateEvidence`; ❌ lineage M04/M04E/M05 в терминах cutover не производится |

### 2.2 Передача evidence по звеньям (пошагово)

| Звено | Факт |
|---|---|
| M04E экспортирует handoff marker | `export_m05_evidence` (backfill.py:1199) → `M05EvidenceExport`; `persist_m05_handoff` (:1118) пишет marker через `M05HandoffPort`. **Порт без реализации** → durable marker не появляется. |
| M05 требует marker | `M05UpstreamEvidence.ready_for_import` требует `marker is not None` (runtime_service.py:199–214) → без durable port'а условие невыполнимо в продакшене. **Разрыв звена.** |
| M05 отдаёт promotion/handoff | `build_m06_handoff_record` (:1476) собирает `PromotionRecord`, `M05PromotionService` пишет через `M05PromotionRecordPort.append_immutable` (:1425). **Порт без реализации.** |
| M06 потребляет | `ServerResolvedHandoffResolver.resolve_gates` (handoff.py:136) → `PartitionHandoffReadPort.load_partition`. **Порт без реализации** → `DependencyGates` всегда `None` → shadow-evidence не пишется ни по одному запросу (`CapabilityGatewayService` уходит в legacy-only). |
| M06 parity → cutover gates | `BoundedParityRunner.run` (parity_runner.py:344) отдаёт `ParityRunReport` (:206) с `report_digest`/counts; store'ы есть (`SqlParityReportStore` parity_store.py:289, `FailClosedParityReportStore` :386, `InMemoryParityReportStore` evidence_store.py:555). Но `GateEvidence`/`GateLineageEvidence` из отчёта **никто не строит**, `GateVerifier` без реализации → `transition_to_canonical` всегда отказывает (`"trusted_full_lineage_gate_verifier_missing"`, cutover.py:902). |

**Точка первого обрыва для cutover:** `M05HandoffPort` (app/m04e_definition_version/backfill.py:1112) —
без неё не появляется marker, без marker'а не выполняется `M05UpstreamEvidence.ready_for_import`,
без этого M05 не выпускает ни одного instance в `PUBLISHED`, а M06 не получает `DependencyGates`.
Даже если закрыть только её, следующим обрывом станет `PartitionHandoffReadPort` (handoff.py:120).

---

## 3. Разрывы (порт без реализации)

> **Статус на 2026-11-04: раздел устарел по существу и сохранён как история.**
> G1, G2, G3, G4, G5, G6, G8, G9 получили реализации — см. таблицу ниже. Актуальное
> состояние ведёт реестр `UNIMPLEMENTED_PORTS` в `application/migration/composition.py`,
> и гейт `tests/migration/integration_seams/test_seam_contract.py` сверяет реестр с кодом,
> поэтому расхождение между этим документом и кодом больше не может остаться незамеченным.
> Документ не переписан целиком намеренно: исходные формулировки полезны, чтобы видеть,
> чем именно был разрыв.

| # | Порт | Реализация (2026-11-04) |
|---|---|---|
| G1 | `M05HandoffPort` | `evidence_ports.SqlM05HandoffEvidenceStore` |
| G2 | `M05PromotionRecordPort` | `evidence_ports.SqlM05PromotionEvidenceStore` |
| G3 | `PartitionHandoffReadPort` | `evidence_ports.SqlPartitionHandoffReadStore` (bulk-режим ещё не принимается builder'ом) |
| G4 | `EvidenceStore` (M04E) | `m04e_ports.SqlM04EEvidenceStore` + таблицы ledger `2026110315` |
| G5 | cutover: repository + 4 верификатора | `m06_cutover/ports.py` (fail-closed: схемы cutover-состояния ещё нет) |
| G6 | `UpstreamGatePort`, `CandidateProofPort`, `CandidateReader` | `m04e_ports.py` |
| G8 | `M05IdentityEvidencePort` / `RuntimeInstanceLifecycle` | были реализованы и на момент ревиза |
| G9 | `ProviderAttemptJournalPort` | `infrastructure/.../attempt_journal.py` поверх таблицы `2026110311` |
| G7 | `ScopeRegistryTransactionManagerPort` и tenant-context порты | `tenant_context.py`; остаётся отсутствующий `TrustedPrincipalResolver` на auth-границе |

Severity: **S1** — блокирует cutover, **S2** — блокирует соответствующий gate/этап, **S3** — шум/риск.

| # | Порт | Объявлен | Что означает | Sev |
|---|---|---|---|---|
| G1 | `M05HandoffPort.append_marker` | app/m04e/backfill.py:1112 | durable marker M04E→M05 отсутствует; `persist_m05_handoff(store=None)` → `RuntimeError` (:1124). Целевые таблицы под это есть: `m04e_definition_version_promotion_evidence` (RLS-политика infra/persistence/rls/policies.py:411) | S1 |
| G2 | `M05PromotionRecordPort.append_immutable` | app/m05/runtime_service.py:1425 | M05 не может записать handoff `MIG-M05-02`; таблица-цель `m05_plugin_instance_promotion_evidence` зарегистрирована (policies.py:434) | S1 |
| G3 | `PartitionHandoffReadPort.load_partition` | app/m06/handoff.py:120 | M06 не читает принятое upstream-evidence → `DependencyGates is None` → shadow parity не накапливается | S1 |
| G4 | `EvidenceStore` (M04E backfill) | app/m04e/backfill.py:280 | bounded page commit/checkpoint CAS для M04E не реализован нигде (в m06 есть одноимённый по смыслу `SqlCanonicalEvidenceStore`, но это другой порт) | S1 |
| G5 | `GateVerifier`, `LegacyOwnerVerifier`, `RollbackVerifier`, `CanonicalRepairVerifier`, `CutoverRepository` | app/m06_cutover/cutover.py:331–343, 557 | вся исполнительная часть cutover — контракт без исполнения; отказы кодируются как `trusted_*_verifier_missing` (:826, :902) | S1 |
| G6 | `UpstreamGatePort`, `CandidateProofPort`, `CandidateReader` | app/m04e/backfill.py:265, :271, :258 | перепроверка owner/scope/definition/artifact в транзакции не реализована; `run_page` отказывает с `trusted_proof_or_source_port_missing` (:374) | S2 |
| G7 | `ScopeRegistryTransactionManagerPort` (+ `OwnerRegistryScopeRepositoryPort`, `ScopeRegistryGateRepositoryPort`) | app/scope_registry_adapter/ports.py:92–137 | `ScopeRegistryApplicationAdapter(transactions=None)` → `scope_registry_transaction_port_missing` (adapter.py:106–112); конструктор менеджера транзакций отсутствует в src | S2 |
| G8 | `M05IdentityEvidencePort` / `RuntimeInstanceLifecycle` | app/m05/runtime_service.py:651, :1088 | *реализованы* (`SqlAlchemyM05IdentityEvidence` :2234, `SqlAlchemyRuntimeInstanceLifecycle` :2040) — см. §4 (сироты), не разрыв | — |
| G9 | `ProviderAttemptJournalPort` (вне M-цепочки, для полноты) | app/agent_runs/attempts.py:589 | реализации SQL-журнала попыток нет; блокер `agent_run_provider_attempt_journal_unwired` честный | S3 |

---

## 4. Сироты (реализация без вызывающего)

Проверка: поиск `ИмяКласса(` по всему `src/prodavan` (не только по определению).

| # | Реализация | Локация | Комментарий |
|---|---|---|---|
| O1 | `SqlAlchemyScopeBackfillAdapter` | app/m04_scope/scope_persistence.py:630 | 0 конструкторов в src |
| O2 | `M04EDefinitionVersionPersistenceAdapter` | infra/migration_definition_version/adapter.py:142 | 0 конструкторов в src (только тесты); при этом его `blockers` — основной источник правды о готовности M04E |
| O3 | `SqlAlchemyAutomationIngressRepository` | infra/automation_migration/ingress.py:76 | 0 конструкторов: `AutomationIngress` вообще не создаётся в src → 8 `*_unwired`-блокеров automation ingress (ingress_service.py:780–1053) срабатывают всегда |
| O4 | `SqlAlchemyAutomationOutboxRepository` | infra/automation_migration/outbox_repository.py:59 | 0 конструкторов |
| O5 | `SqlAlchemyRunLeaseStore` | infra/agent_control_plane/lease_store.py:461 | 0 конструкторов; документирует себя как «no host» (шапка файла) |
| O6 | `SqlAlchemyAgentRunRetryJournal` | app/agent_runs/retry.py:1201 | 0 конструкторов |
| O7 | `SqlAlchemyUnknownOutcomeJournal` | app/agent_runs/reconcile.py:1488 | 0 конструкторов |
| O8 | `build_sql_resolve_plugin_instance_port` | app/m05/instance_persistence.py:2439 | фабрика есть, вызовов вне модуля нет (только `__all__` :2489) |
| O9 | `build_shadow_gateway` | app/m06_grants_gateway/composition.py:69 | composition root без вызова; `CapabilityGatewayService` собирается только тестами |
| O10 | `build_durable_equipment_service` | app/migration/equipment_domain/composition.py:40 | упоминается только в докстрингах и `__all__`; API-роутера над ним нет |
| O11 | `M05PromotionService`, `BoundedParityRunner`, `CutoverCommandService`, `ScopeMigrationCoordinator`, `DefinitionVersionEvidenceBackfill`, `InstanceImportService`, `ScopeRegistryApplicationAdapter` | см. §2.1 | все инстанциируются исключительно в `tests/**` |

**Важно:** сироты O1–O4, O6–O7 — это не «мёртвый код случайно»: это компоненты, покрытые тестами и
RLS-реестром, у которых отсутствует только проводка (см. §8, вердикт «забытая проводка»).

Машиночитаемое подмножество сирот закреплено в тесте (`REVIEWED_ORPHANS`): реализации портов из §2,
которые конструируются только внутри своего модуля или в невызываемой фабрике —
`ScopeRegistryApplicationAdapter`, `SqlAlchemyTrustedScopeTransactionPort`,
`SqlAlchemyRuntimeInstanceLifecycle`, `SqlAlchemyM05IdentityEvidence`,
`SqlAlchemyInstanceImportStore`, `SqlAlchemyInstanceQuarantineStore`, `SqlAlchemyRedactedAuditSink`.
Появление у любого из них внешнего вызывающего без записи в отчёте валит
`test_implementations_have_a_caller_or_are_reviewed_orphans`.

---

## 5. Несовместимости (одно понятие — разные имена/типы по обе стороны)

| # | Сторона A | Сторона B | Суть | Sev |
|---|---|---|---|---|
| I1 | `ScopeRegistryApplicationAdapter.resolve_active(...) -> ScopeAdapterResult` (app/scope_registry_adapter/adapter.py:92) | `ResolveScopePort.resolve_active(...) -> ScopeResolutionEvidence \| None` (app/m04_scope/scope_migration.py:640) | Имя метода совпадает, тип — нет. `ApplicationIdentityResolutionAdapter` (m06 identity_adapter.py:91–101) делает `isinstance(scope_evidence, ScopeResolutionEvidence)` → для этого адаптера результат всегда `False` → `IdentityResolutionError("m04_scope_evidence_missing")`. **M06 identity не может быть удовлетворён ни одним существующим app-адаптером M04.** | S1 |
| I2 | M05 `PromotionRecord` (runtime_service.py:1396): counts + `canonical_digest` + `gate_evidence` | M06 `AcceptedPartitionEvidence` (handoff.py:47): требует `accepted_sources: tuple[AcceptedSourceKey]` (source_kind/id/schema/digest), `inventory_digest`, `m05_instance_digest`, `m04_scope_evidence_id`, `m04_rls_evidence_id`, `m04e_definition_version_evidence_id`, `atomic_evidence_id`, `m06_schema_identity_digest` | M05-запись не содержит инвентаря источников и не несёт evidence-ID по стадиям; `__post_init__` (handoff.py:97–104) требует непустой канонически упорядоченный inventory → запись M05 **не представима** как `AcceptedPartitionEvidence` без нового mapper'а и новых полей | S1 |
| I3 | `writer_generation: int ≥ 0` (M05, runtime_service.py:1352/1396) | `writer_epoch: int > 0` (`AcceptedPartitionEvidence` handoff.py:87; `DependencyGates.trusted_writer_epoch`; cutover `LegacyWriterDrainEvidence.writer_epoch > 0` :120) | Разные имена **и** разные домены одного понятия: партиция в генерации 0 не может выпустить M06-evidence. Нужна явная нормализация (например `epoch = generation + 1`) в одном месте | S2 |
| I4 | `checkpoint_digest` требуется: `M04ScopeGateEvidence` (runtime_service.py:103), `M05RuntimeGateEvidence` (:130) | M04E `BackfillCheckpoint` (backfill.py:133) **не имеет** `.digest` (только `__post_init__`); M05 `ImportCheckpoint.digest` (:410) есть | Дайджест M04E-чекпоинта невычислим из типа M04E — его должен «придумать» несуществующий composer | S2 |
| I5 | Словарь gate-имён M04E: `MIG-M04-01`, `MIG-M04-02`, `MIG-M04E-01`, `active_typed_parent_scope`, `same_definition_version`, `immutable_artifact` (backfill.py:1218–1221) | M05 `required_gates` = те же 6 (runtime_service.py:177–178) **плюс** привязка `MIG-M04-01 → m04_scope.parent_validation_digest`, `MIG-M04-02 → m04_scope.canonical_digest` (:180–189) | M04E экспортирует один digest на gate; M05 ожидает, что `MIG-M04-01` совпадёт с `parent_validation_digest` отдельного объекта `M04ScopeGateEvidence`, который конструируется только в тестах. Связка «M04-01 → parent_validation_digest» нигде не производится | S2 |
| I6 | Gate-словари трёх поколений: `HandoffGate(name, passed, evidence_digest)` (m04e) / `PromotionGateEvidence(name, passed, evidence_digest, reason_code)` (m05) / `GateLineageEvidence(stage, evidence_id, evidence_digest, parent_…)` + `GateEvidence(m04, m04e, m05)` (cutover) | — | Ни один конвертер между ними не существует; имена стадий `M04_scope`/`M04E_definition_version`/`M05_runtime` ≠ `M04`/`M04E`/`M05` ≠ `MIG-M04-xx` | S2 |
| I7 | Порядок источников M04E/M05: курсорный `(source_kind, source_id, schema_version)` по `after_source_id` | `AcceptedPartitionEvidence` требует **сортированный** inventory по `(kind, id, schema, digest)` (handoff.py:99–102) | Composer обязан переупорядочивать; при прямом переносе курсорного порядка — `ValueError` | S3 |
| I8 | Канонизатор дайджеста: `_digest` (m04e backfill.py:18), `_digest` (m05 runtime_service.py:54), `digest`/`_canonical_json` (m06 adapter.py:1215/1219), `digest` (parity_runner через adapter) — все с `ensure_ascii=True` | `_lineage_payload_digest` (cutover.py:510) — `json.dumps(sort_keys=True, separators=(",",":"))` **без** `ensure_ascii` и без `default=` | Для ASCII payload совпадает; для не-ASCII (кириллические capability_key/tenant-метки) байты расходятся → кросс-шовное сравнение дайджестов даст ложный mismatch | S2 |

---

## 6. Дубли (два определения одного понятия)

| # | Понятие | Определения | Риск |
|---|---|---|---|
| D1 | `CapabilityGatewayService` | app/capability_gateway_service.py:180 (production, `CapabilityDecision`/`GatewayDecision`) **и** app/m06_grants_gateway/adapter.py:798 (shadow) | Два класса с одним именем в разных пакетах; импорт по имени из корня легко путает shadow с production. Переименовать shadow → `ShadowGrantGateway` |
| D2 | `PromotionRecord` | app/m04e/backfill.py:1091, app/m05/runtime_service.py:1396, app/m06_cutover/cutover.py:545, app/canonical_grants_promotion/contracts.py | Четыре несовместимых формы; ни один конвертер не существует |
| D3 | `GateEvidence` | app/m06_cutover/cutover.py:154 **и** app/migration/automation_ingress/ingress_service.py:437 (другие поля) | Разные поля при одинаковом имени |
| D4 | `HandoffStatus` | app/m04e/backfill.py:39 **и** app/m05/runtime_service.py:93 | M05 импортирует M04E-версию как `M04EHandoffStatus` и держит свою — сравнение `is` между ними даст ложь |
| D5 | `FinalDeltaEvidence` | app/m05/runtime_service.py:1336 **и** app/m06_cutover/cutover.py:127 | Разные поля/семантика |
| D6 | `PartitionKey` | app/m06_cutover/cutover.py:42 **и** app/migration/automation_outbox/outbox.py:36 | Cutover ждёт свой тип; конвертера нет |
| D7 | Паритет | app/m04e/parity.py:129 `PartitionParityReport`, app/m05/runtime_service.py:1353 `ParityEvidence`, app/m06/parity_runner.py:206 `ParityRunReport`, app/m06/parity_store.py:88 `ParityReportRecord` | Четыре модели паритета на одну цепочку; клей между ними отсутствует |
| D8 | Канонизатор JSON/дайджеста | m04e `_digest`, m05 `_digest`, m06 `digest`+`_canonical_json`, cutover `_lineage_payload_digest`, ingress `_json_digest`, `parity_store._report_digest` | Шесть независимых канонизаторов — источник I8 и будущих «необъяснимых» mismatch'ей |

---

## 7. Проводка в процесс: composition roots

| Что | Факт |
|---|---|
| `core/wiring.py` | нет ни одного упоминания `migration`/`m04`/`m05`/`m06` (проверено `Select-String` по файлу) |
| `api/` | роутеров над M04–M06 нет; HTTP-слой касается только agent runs (`api/migration_agent_runs/**`) |
| Единственные реальные composition roots в src | `build_sql_resolve_plugin_instance_port` (app/m05/instance_persistence.py:2439, без вызова), `build_shadow_gateway` (app/m06/composition.py:69, без вызова), `build_parity_runner` (там же :93, без вызова), `build_durable_equipment_service` (equipment composition.py:40, без вызова), `build_intent_dispatcher` (infra/agent_control_plane/intent_consumer.py:692 — **единственный, у которого есть реальные вызывающие**: api/migration_agent_runs/dependencies.py:260 и core/infra/agent_run_worker_resource.py:925) |
| Что это значит | migration-цепочка полностью «библиотечная»: контракты, DDL и тесты есть, процесса нет. Cutover-gate не может быть достигнут ни при каком состоянии БД |

Предлагаемая правка (вне моей зоны, для владельца): единый `application/migration/composition.py`
с явными обязательными аргументами (никаких `=None`), собирающий M04→M04E→M05→M06 из
infra-адаптеров, и один вызов из `core/wiring.py`. Внутри — три отсутствующих адаптера из §3 (G1–G3)
и mapper'ы из §5 (I2, I3, I6, I7).

---

## 8. Класс «константа-блокер»: документированная граница или забытая проводка

Метод: собраны все литералы, привязанные к имени/параметру `blocker` (`NAME = "…"`, `blocker="…"`,
`"blocker": "…"`), покрывающие `*_unwired` / `*_not_wired` / `*_missing`; для каждого проверено, есть ли
реализация зависимости в `src/prodavan` и есть ли конструктор/вызов этой реализации в продакшн-пути.
Полный список (52 кода) зафиксирован в тесте (`BLOCKER_CLASSIFICATION`), чтобы новые блокеры нельзя было
добавить без классификации.

Уже исправленные до этой ревизии (не переоценивались, приняты как факт задания):
`agent_control_plane_dispatcher_unwired`, `web_chat_stream_facade_unwired`, `apply_receipt_store_unwired`,
`resource_persistence_adapter_unwired`, `agent_run_retry_due_start_transition_unwired`.

### 8.1 Дефекты: забытая проводка (зависимость реализована, никто не устанавливает)

| Блокер | Где | Реализация зависимости | Вердикт |
|---|---|---|---|
| `agent_run_lease_storage_missing` | app/agent_runs/lease.py:78 | **есть**: `SqlAlchemyRunLeaseStore` (infra/agent_control_plane/lease_store.py:461, 542 строки, включая `RunLeaseFenceStore` :116, `claim_scope` :477, `park/fence` statements :389/:427) | **Забытая проводка.** Текст блокера («storage missing») устарел: storage реализован, отсутствует композиция `LeaseManager(store=…)`. Волна фиксирует это как «Execution adapter + lease store — In progress», т.е. дефект известен, но константа вводит в заблуждение |
| `agent_run_retry_journal_unwired` | app/agent_runs/retry.py:99 | **есть**: `SqlAlchemyAgentRunRetryJournal` (retry.py:1201) в том же модуле | **Забытая проводка**: журнал реализован, конструкторов в `src/prodavan` нет |
| `agent_run_unknown_outcome_journal_unwired` | app/agent_runs/reconcile.py:128 | **есть**: `SqlAlchemyUnknownOutcomeJournal` (reconcile.py:1488) | **Забытая проводка** |
| `agent_run_durable_claim_and_lease_unwired` / `agent_run_execution_port_unwired` | app/agent_runs/dispatcher.py:147, core/infra/agent_run_worker_resource.py:140 | **есть**: `FencedLeaseStore`/`ControlPlaneAgentRunExecutionAdapter` (execution_adapter.py:165,261) + реализованный lease store | **Забытая проводка**: `ControlPlaneAgentRunExecutionAdapter(` и `AgentRunUnknownOutcomeReconciler(` не конструируются нигде в `src/prodavan` |
| `agent_run_provider_outcome_reconciliation_unwired` | app/agent_runs/dispatcher.py:148, app/agent_runs/service.py:30, app/agent_runs/reconcile.py:5 | **есть**: `AgentRunUnknownOutcomeReconciler` (reconcile.py:957) + `SqlAlchemyUnknownOutcomeJournal` (reconcile.py:1488) | **Забытая проводка**: reconciler реализован, в процесс не проведён; код дублирует причину `agent_run_unknown_outcome_journal_unwired` |
| `intent_handler_not_wired` | app/agent_runs/dispatcher.py:145 | **зависит от предыдущей**: dispatcher собирается (`build_intent_dispatcher`, intent_consumer.py:692; вызывается из api/dependencies.py:260 и worker_resource.py:925), но реестр handler'ов пуст | Следствие забытой проводки #выше; текст блокера маскирует причину (нет execution port, а не «handler не написан») |

### 8.2 Документированные границы (зависимости нет по дизайну)

| Блокер(ы) | Где | Почему граница |
|---|---|---|
| `agent_control_plane_atomic_transaction_adapter_unwired` | app/agent_control_plane/ports.py:17; отдаётся из service.py:786 только когда `_transaction is None` | Зависимость **реализована** (`SqlAlchemyAgentControlPlaneRepository`, repo.py:50) и **проведена** в API (api/migration_agent_runs/dependencies.py:245,306). Блокер — остаточный fail-closed guard для композиций без порта, а не дефект |
| `agent_run_provider_attempt_journal_unwired`, `agent_run_provider_attempt_table_missing` | app/agent_runs/attempts.py:73,76 | нет ни реализации журнала, ни таблицы — честная граница |
| `agent_run_retry_schedules_table_missing`, `agent_run_unknown_outcomes_table_missing` | app/agent_runs/retry.py:100, reconcile.py:129 | DDL-граница (таблиц нет в реестре моделей); требует сверки с `Base.metadata` при снятии freeze |
| `agent_control_plane_aggregate_cursor_missing` | api/v1/agent_runs.py:613,842 | DDL-граница (нет курсорной колонки) |
| `agent_run_host_dependencies_unwired`, `agent_run_run_input_source_unwired`, `agent_run_turn_runner_unwired` | core/infra/agent_run_worker_resource.py:139,141,143 | host-композиция: перечисляет ровно отсутствующие внешние зависимости |
| `agent_run_unknown_case_journal_unwired`, `agent_run_control_plane_actor_unresolved` | app/agent_runs/execution_adapter.py:126,129 | нет журнала/резолвера актора — граница |
| `web_stream_session_resolver_unwired`, `durable_run_sequence_index_missing`, `web_stream_run_index_evidence_missing` | app/web_facade_adapter/runtime_factory.py:82,83,86 | границы web-фасада (часть уже закрыта фиксом `web_chat_stream_facade_unwired`) |
| `scope_registry_transaction_port_missing` | app/scope_registry_adapter/adapter.py:109 | менеджер транзакций M04 не реализован нигде → граница **и одновременно** первичная причина G7 |
| `agent_run_requeue_transition_unwired` | app/agent_runs/lease.py:81 | операции `agent_run.requeue` в контроль-плейне нет по дизайну: репозиторий принимает только `start/fail/cancel/timeout` (docstring lease.py:49–59). Освобождение захваченного run'а идёт через истечение TTL, а не через вернувшуюся операцию — честная граница |
| `equipment_domain_transaction_adapter_unwired` | app/migration/equipment_domain/transactions.py:32 | Композиция `build_durable_equipment_service` реализована и передаёт `transaction=repository` → блокер для неё недостижим (`transactions.py:11` прямо это фиксирует). Дефект другой: **композиция не вызывается из API** (O10) |
| 8 блокеров automation ingress: `subscription_resolver_unwired`, `signature_verifier_unwired`, `project_trigger_resolver_unwired`, `trigger_fence_unwired`, `m04_scope_resolver_unwired`, `promotion_gate_verifier_unwired`, `m04e_definition_resolver_unwired`, `atomic_ingress_transaction_unwired`, `durable_quarantine_unwired` (+ `*_unavailable`, `*_outcome_unknown`) | app/migration/automation_ingress/ingress_service.py:780–1065 | Сервис `AutomationIngress` не конструируется в src, но его infra-репозиторий (`SqlAlchemyAutomationIngressRepository`, infra ingress.py:76, 515 строк с `verify_m04*`) реализован и не проведён → **смешанный случай**: сами резолверы — граница, а неиспользованный репозиторий — забытая проводка (O3) |
| `durable_quarantine_unwired`, `resource_persistence_adapter_unavailable`, `m04_*_unavailable` | см. выше / resources/api/composition.py:126 | fallback-коды после неудачи композиции — границы |

### 8.3 Итог по классу

Из проверенных блокеров **7 относятся к «забытой проводке»** (`agent_run_lease_storage_missing`,
`agent_run_retry_journal_unwired`, `agent_run_unknown_outcome_journal_unwired`,
`agent_run_provider_outcome_reconciliation_unwired`, `agent_run_durable_claim_and_lease_unwired`,
`agent_run_execution_port_unwired` и следствие `intent_handler_not_wired`). `agent_run_requeue_transition_unwired`
пересмотрен как **граница**: операции `agent_run.requeue` в контроль-плейне нет по дизайну.
Остальные — документированные границы либо DDL-долги, требующие сверки при снятии freeze.
Полный перечень вердиктов зафиксирован в `BLOCKER_CLASSIFICATION` теста; новый
блокер без вердикта валит тест, а вердикт, которого нет в этом отчёте, — тоже.

---

## 9. Что нужно исправить (конкретно, без правок в этой задаче)

1. **I1 (S1).** `ScopeRegistryApplicationAdapter.resolve_active` (adapter.py:92) должен реализовать
   `ResolveScopePort` (scope_migration.py:640): вернуть `ScopeResolutionEvidence | None`. Сейчас M06
   identity гарантированно падает на `isinstance` (identity_adapter.py:100).
2. **G1 (S1).** Реализовать `M05HandoffPort` поверх append-only
   `m04e_definition_version_promotion_evidence` (RLS: policies.py:411): INSERT + exact-retry SELECT,
   идемпотентность по `record_id` из `persist_m05_handoff` (backfill.py:1156–1176).
3. **G2 + I2 + I3 (S1).** Реализовать `M05PromotionRecordPort` поверх `m05_plugin_instance_promotion_evidence`
   (policies.py:434) и добавить mapper `M05 PromotionRecord → AcceptedPartitionEvidence`, восстанавливающий
   `accepted_sources` из `InstanceImportRecord`-строк (source_kind/id/schema/digest), `inventory_digest`
   (`inventory_digest()`, handoff.py:115), `m05_instance_digest = digest(identity.digest…)`, и
   нормализующий `writer_generation → writer_epoch`.
4. **G3 (S1).** Реализовать `PartitionHandoffReadPort.load_partition` чтением той же таблицы под RLS;
   без него `ServerResolvedHandoffResolver` всегда вернёт `None`.
5. **G4 (S1).** Реализовать M04E `EvidenceStore` (page-commit с CAS чекпоинта) — целевые таблицы уже
   зарегистрированы (`m04e_definition_version_promotion_evidence`).
6. **G5 + I6 (S1).** Mapper `ParityRunReport` + M05 promotion → `GateEvidence`/`GateLineageEvidence`
   (стадии M04/M04E/M05, parent-ID/дайджест-цепочка), затем `GateVerifier` и `CutoverRepository`.
7. **I4 (S2).** Добавить `BackfillCheckpoint.digest` (или хелпер) в M04E, чтобы `checkpoint_digest`
   у `M04ScopeGateEvidence`/`M05RuntimeGateEvidence` был вычислим.
8. **I8/D8 (S2).** Один канонизатор (`prodavan/core/canonical_json.py`) вместо шести копий;
   в cutover добавить `ensure_ascii=True` и `default=`.
9. **§7.** Один composition root для цепочки + вызов из `core/wiring.py`; сделать обязательными
   аргументы `upstream`/`deviations` (сейчас `build_sql_resolve_plugin_instance_port(upstream=None)`
   молча собирает deny-everything порт, instance_persistence.py:2444).
10. **D1–D7.** Убрать дубли имён: `CapabilityGatewayService` (shadow) → `ShadowGrantGateway`;
    `HandoffStatus`, `FinalDeltaEvidence`, `PartitionKey`, `PromotionRecord`, gate-словари — по одному
    определению в `application/migration/contracts.py` с явными конвертерами.

---

## 10. Приёмочные артефакты и границы ревизии

Покрыто: карта швов (§2), разрывы (§3), сироты (§4), несовместимости (§5), дубли (§6), отсутствие
проводки в процесс (§7), классификация blocker-кодов (§8), список правок (§9).

Тест [`test_seam_contract.py`](../../apps/api/tests/migration/integration_seams/test_seam_contract.py)
не импортирует `prodavan.*` вовсе: он читает исходники и `ast`, поэтому не требует настроек, БД и
фикстур. Четырнадцать проверок:

| Проверка | Что ловит |
|---|---|
| `test_every_seam_port_is_declared_at_the_reviewed_location` | порт переехал/перестал быть `Protocol` |
| `test_declared_ports_have_an_implementation_or_a_reviewed_gap` | новый порт без реализации и без вердикта |
| `test_implementations_have_a_caller_or_are_reviewed_orphans` | новая сирота без записи в `REVIEWED_ORPHANS` |
| `test_chain_is_not_wired_into_the_process` | цепочка «незаметно» проведена в `api/`/`core/` (тогда §7 устарел) |
| `test_m04_application_adapter_still_violates_the_resolve_scope_contract` | I1 молча «исправлен» без пересмотра §5 |
| `test_m04e_handoff_marker_still_carries_every_field_m05_reads` | marker потерял поле, которое читает M05 |
| `test_m05_promotion_record_still_cannot_satisfy_m06_partition_evidence` | I2 зафиксирован: пересечение имён полей пусто, разрыв не «схлопнулся» |
| `test_writer_epoch_and_writer_generation_stayed_different` | I3: имена/домены не слились без mapper'а |
| `test_gate_name_vocabularies_match_upstream_and_differ_downstream` | дрейф gate-имён m04e↔m05 и словаря `GateStage` |
| `test_digest_canonicalizer_is_unique_except_for_the_reviewed_deviation` | новый канонизатор без `ensure_ascii` / cutover «починили» без пересмотра I8 |
| `test_duplicate_contract_names_match_the_review` | появился/исчез дубль контракта |
| `test_every_emitted_blocker_code_is_classified` | новый `blocker=`-код без вердикта «граница vs забытая проводка» |
| `test_classified_blockers_still_exist` | вердикт ссылается на исчезнувший код |
| `test_defect_verdicts_are_recorded_in_the_review` | вердикт есть в тесте, но не в §8.1 (дрейф отчёта и теста) |

Область сканирования блокеров: все литералы `blocker=`/`"blocker":` в `src/prodavan` плюс
константы-блокеры внутри `application/migration/**` и `infrastructure/persistence/**`. Константы
`application/agent_runs/**`, `core/infra/**`, `api/**` исключены из сканирования (они относятся к
host-wiring агентских run'ов, см. §8.1), но их вердикты зафиксированы в `BLOCKER_CLASSIFICATION` и
проверяются на актуальность.

Границы: ничего не исполнялось; DDL/ORM не проверялись на живой БД; `Base.metadata` для
`*_table_missing`-кодов не сверялся (см. §8.2) — это отдельная задача при снятии freeze.
Негативные/граничные случаи, зафиксированные в отчёте и тесте: партиция с `writer_generation=0`
(против `writer_epoch > 0`), не-ASCII payload в дайджесте, курсорный порядок источников против
сортированного inventory, отсутствие `handoff_marker`, `transactions=None` в M04-адаптере,
`store=None` в `persist_m05_handoff`, `upstream=None` в `build_sql_resolve_plugin_instance_port`
(молча собирает deny-everything порт).

## 11. Freeze

Тесты, сборки, компиляция, линтеры, форматтеры, `pytest`, `alembic`, DDL и git-проверки намеренно
**не запускались**. Код в `apps/api/src/**` не изменялся; изменены только:
`docs/08-migration/integration-seams-review.md` (этот файл) и
`apps/api/tests/migration/integration_seams/test_seam_contract.py` (новый тест).
