# Migration implementation static risk review

Статус: documentation-only статический review implementation-файлов и migration contracts. Baseline: 2026-11-01. Рассмотрены M04 scope/M04.e, M05, M06/cutover, resources/runtime materialization, equipment, automation/outbox, web API/SSE и относящиеся к ним migration reports. Нумерация строк ниже относится к указанным файлам на baseline review.

Цель — зафиксировать конкретные ошибки границ/логики, способные привести к потере evidence, повторному побочному эффекту, неверному allow или утечке, если соответствующий путь подключён. «Gated» означает, что описанный риск не является разрешением использовать этот путь: promotion records для M04/M05/M06 сейчас blocked и положительные записи должны оставаться выключенными.

## Findings

### R-01 [Critical] M06 выбирает capability по первому совпавшему имени, не отвергая дубликат

В `migration/m06_grants_gateway/adapter.py:264-268` `next(...)` возвращает первый grant с совпавшим `name`; последующий grant с тем же именем, но другой decision/constraints игнорируется. Такой GrantSet неоднозначен: порядок JSON-массива начинает определять policy, а mapping digest при этом хеширует весь source. Источник может выглядеть replay-стабильным, но проверяемая capability не имеет однозначного смысла.

**Эффект:** неверная shadow parity и потенциально слишком широкая/узкая canonical mapping при последующей интеграции; это также делает источник порядка-зависимым.

**Remediation:** до выбора потребовать ровно одно вхождение capability name, иначе quarantine с отдельным stable reason; проверять полный GrantSet на duplicate names до mapping. Persistence должен хранить conflict/quarantine неизменно и не считать такой источник mapped parity.

### R-02 [Critical] M06 ingest не валидирует время жизни исходного grant

`LegacyGrantSource` проверяет timezone-awareness `issued_at`/`expires_at`, но не их порядок (`migration/m06_grants_gateway/adapter.py:75-80`). В `observe()` эти поля входят в digest, однако не сравниваются с `checked_at` и не влияют на классификацию (`:229-238`, `:278-306`). Просроченный или ещё не действующий source grant поэтому попадает как `MAPPED_EVIDENCE`; shadow-решение не является импортной классификацией исторического validity.

**Эффект:** истёкшее разрешение может считаться eligible evidence/parity input; перепутанный `expires_at < issued_at` принимается.

**Remediation:** валидировать `expires_at > issued_at`; классифицировать future-issued/expired grants как explicit deny/quarantine на фиксированном trusted timestamp, не как positive mapping; persistence/audit должны сохранять решение и observed-at в той же evidence lineage.

### R-03 [High] M04 import identity/digest допускает гонку и неполное source hashing

`resources.py:217-241` сначала делает `find_import`, затем отдельно пишет конфликт (`record_import`) либо новую версию/binding (`append_resource_version_and_binding`). Port не требует общей транзакции/unique-CAS. При этом digest от кандидата предоставлен извне, а не пересчитан по candidate (`:141-148`, `:217-225`). Два concurrent импортера могут оба увидеть отсутствие mapping и создать записи; вызывающая сторона может передать digest, не соответствующий resource/binding payload.

**Эффект:** duplicate или противоречивая binding/version запись под одним `(source_kind, source_id, schema_version)`, ложный replay и потеря forensic integrity.

**Remediation:** trusted adapter пересчитывает canonical digest из полного source envelope и binding, одной транзакцией выполняет insert-if-absent/unique CAS вместе с version, binding, mapping/quarantine/audit; на конфликте читает winner и сравнивает immutable identity/hash, не перезаписывая первоначальный record. Требуются ORM unique keys, Alembic и persistence adapter.

### R-04 [High] Resource resolution имеет TOCTOU между binding/resource/auth/version чтениями

`resources.py:200-213` независимо читает binding и resource, затем вызывает scope authorization и выбирает version из in-memory `resource.versions`. Контракт не связывает эти чтения одним snapshot/transaction и не сверяет отдельную версию с durable binding row атомарно с использованием.

**Эффект:** конкурентный disable/rebind/archive/version change после проверки может привести к применению уже неавторизованной версии или старого binding.

**Remediation:** persistence resolver должен вернуть единый immutable snapshot binding+resource+version и parent proof в одной tenant-scoped транзакции; применение/материализация должна CAS-проверить binding/version/lifecycle generation непосредственно перед side effect. Нужны durable FK/unique constraints, RLS и Alembic; M04 scope gate остаётся обязательным.

### R-05 [High] Equipment evidence «verified» не является атомарным/одноразовым admission для side effect

`equipment_evidence_handoff/service.py:44-58` сначала проверяет видимость/provenance/membership, затем отдельный matching caller получает decision и может продолжить; между ними данные могут измениться. Проверка reused ID необязательна, если `evidence_read` отсутствует (`:79-82`), а `handoff()` отдельно пишет evidence/quarantine (`:44-51`) без транзакционного контракта с последующим select/procurement. Bundle-валидация (`equipment_evidence_handoff/contracts.py:91-126`) не связывает `offer_id` и `candidate_group_id` с tenant/project фактическими relational rows сама по себе.

**Эффект:** evidence ID может быть переиспользован с другой bundle без read port; provenance/membership может устареть между authorize и действием; side effect возможен без durable verified evidence либо append может не совпасть с ним.

**Remediation:** обязательный durable evidence store с unique `(tenant, project, evidence_id)` + digest conflict; одна trusted transaction/CAS связывает повторное чтение offer/group/source chain, evidence append и selection/procurement intent. Не принимать «verified» как capability; matching снова проверяет policy. Требуются tenant/project composite keys, association/FK/trigger из equipment schema, Alembic и persistence integration.

### R-06 [High] Resource materialization не сериализует конкурентный apply с проверкой поколения

`resource_materialization/service.py:51-57`, `:78-90`, `:103-112` читает resource, bindings и version последовательно, а затем возвращает operations с `desired_generation`; сам resolver не выполняет CAS. Required failure очищает весь набор (`:114-123`), но optional failure оставляет частичный набор операций. Без downstream transaction, которая проверяет поколение и ожидаемый полный selection set, target workspace может применить устаревший/частичный результат при конкурентной смене scope/версии.

**Эффект:** смешанное поколение materialization либо неполное состояние workspace, если caller трактует наличие operations как успешную полную сборку.

**Remediation:** задавать явную atomicity policy для optional selections; связывать selection manifest digest и snapshot; единственный apply port атомарно сверяет revision/generation, bindings и весь operation set, иначе no-op/conflict. Persistence/revision CAS требуется; web не должен напрямую считать промежуточную resolution success.

### R-07 [High] Cutover evidence append и переключение owner разделены

`migration/m06_cutover/cutover.py:157-163`, `:171-180`, `:203-209` сначала append-ит promotion evidence, затем отдельно CAS-ит `PartitionCutover`. Сбой/конкурентный CAS оставляет evidence о переходе без state transition; replay promotion при существующем evidence возвращает текущее состояние без проверки, что оно соответствует evidence (`:139-146`). `ParityGate` принимает caller-provided booleans/counters/digests и проверяет лишь непустоту/нули (`:55-64`), не доказывая подпись/partition lineage или, например, временное окно.

**Эффект:** replayable command и audit record могут расходиться с фактическим owner, а недоверенный/устаревший gate snapshot способен пройти структурную проверку.

**Remediation:** persistence реализует одну транзакцию с partition row CAS, lease/fence проверкой, immutable evidence append и уникальным command ID; reload replay подтверждает record digest, итоговое revision и owner. Gate должен разрешаться по trusted evidence IDs/digests, с exact partition/window/schema/revision linkage, не по непроверенным flags. Нужны ORM/Alembic unique/locking constraints и trusted repository. Пока это не реализовано, cutover запрещён.

### R-08 [High] Automation worker применяет stale mutable object без lease-token CAS

`automation_service/service.py:89-111` проверяет предварительно считанный `TaskRun`, затем вызывает `claim_task`; после claim `start_task_run()` меняет объект и сохраняет его без lease identity (`:113-120`). Аналогично completion/failure сохраняют переданный объект (`:121-145`). Delivery transport запускается после CAS claim, но результат также сохраняется только по объекту (`:161-193`). Outbox API содержит `owner_id`, но не lease generation/token (`automation_service/outbox.py:40-64`). `automation_persistence/service.py:108-110` лишь валидирует expiry локально, а completion передаёт токен в port; само atomic predicate здесь не гарантирован.

**Эффект:** worker, потерявший lease и уступивший takeover, может завершить/перезаписать run/delivery или повторить side effect; внешняя доставка остаётся at-least-once и нуждается в неизменном idempotency key.

**Remediation:** все start/complete/retry/dead-letter требуют persisted opaque lease token/generation и status+owner+generation(+expiry) CAS в DB; перед dispatch проверяется владение, а destination дедуплицирует стабильный delivery ID/body digest. На crash/unknown outcome retry только с тем же ключом. Требуются ORM/Alembic, transaction repository и worker/outbox integration.

### R-09 [High] Outbox worker может сохранить raw exception text в durable error field

`automation_service/outbox.py:125-143` пишет `str(exc)` в retry/dead-letter. `automation_service/service.py:176-187` аналогично копирует исключение transport в `last_error`; `_error()` в persistence boundary только проверяет непустоту (`automation_persistence/service.py:102-105`). Ошибка HTTP клиента/драйвера может содержать URL, hostname, response fragment, query credentials или payload.

**Эффект:** секреты/PII/внутренние адреса попадают в DB, tenant-visible diagnostics или audit/logging downstream.

**Remediation:** хранить только stable error class/code, ограниченный sanitized diagnostic и digest; секретную причину держать в защищённых server logs с redaction. Ввести размеровые пределы и гарантировать redaction на transport adapter до persistence. Это application/persistence security change, не решается только Alembic.

### R-10 [Critical] Outbound webhook destination требует SSRF policy, которой target boundary не доказывает

`automation_service/contracts.py:68-78` несёт произвольный `destination`, а `AutomationService.deliver()` передаёт его напрямую `DeliveryTransportPort` без проверки scheme, DNS/IP или redirect policy (`automation_service/service.py:151-175`). Automation cutover contract требует URL validate/normalize при import, но не определяет/не подтверждает enforcement на каждом send (`docs/08-migration/automation-cutover-adapter.md:46-55`). DNS rebinding/redirect способны обойти проверку только при создании endpoint.

**Эффект:** серверный HTTP worker может обращаться к loopback, link-local/cloud metadata, внутренним control-plane адресам или пересылать подписанные события через redirect.

**Remediation:** транспортный egress guard валидирует URL при каждом соединении и redirect hop, разрешает только HTTP(S) policy, блокирует private/reserved IP после DNS resolution, защищает от rebinding, ограничивает redirects/ports и запрещает credential-bearing URLs; TLS verify обязателен. Секрет подписи выбирается только после scope/endpoint resolution. Добавить security-reviewed transport tests при снятии verification freeze; endpoint allowlist/policy version следует хранить durable.

### R-11 [High] Browser SSE parser теряет framing/cursor и может зависнуть на некорректном/неполном потоке

`apps/web/src/api/client.ts:57-71` делит только по `\n\n`, читает только `data:`, не обрабатывает CRLF/comments/id/event и отбросывает остаточный final frame на EOF; `JSON.parse`/shape не обёрнуты в stable protocol error. Abort читателя зависит от signal, но при исключении reader не отменяется. Следовательно transport cursor не может быть валидирован/зафиксирован после успешной UI обработки, и canonical UUID replay contract не выполняется.

**Эффект:** пропуск/дублирование событий при reconnect, несовместимость с валидными SSE кадрами, malformed payload может ронять workflow; невозможно безопасно возобновить ровно с последнего применённого event.

**Remediation:** standards-compliant SSE parser (CRLF, multiline data, comments, `id`, `event`, EOF frame), проверка совпадения transport и JSON envelope, schema validation, duplicate suppression и cursor commit только после обработки; `reader.cancel()` в cleanup. Web integration обязана передавать Last-Event-ID к run-aware server resolver и не POST-ить новый turn при неизвестном результате. Durable turn/run/event index нужен для UUIDv5 replay.

### R-12 [High] Typed web facade пока не связывает initial stream/run и reconnect с durable scope snapshot

`web_api/facade.py:53-56` `run_chat` принимает только auth/request, без отдельного cursor параметра. В отчётах зафиксировано, что auth context conversion не переносит request session, initial legacy event rows не имеют run ID и фильтрация conditional на cursor (`docs/08-migration/web-cutover-implementation.md:406-434`; `web-sse-bridge-contract.md:302-347`). Клиент уже отправляет `Last-Event-ID` (`apps/web/src/api/client.ts:38-49`), но этот интерфейс сам по себе не гарантирует forwarding до legacy service.

**Эффект:** reconnect может создать второй turn или смешать session-local последовательность разных runs; route/payload ID подмены опасны, если интегратор трактует payload как authority.

**Remediation:** на сервере до legacy invocation проверять transport + application scopes и разрешать `(tenant, project, session, turn_id) -> run_id`; durable idempotent claim и event index дают run-aware exclusive cursor; каждый event проверяется по project/session/run перед сериализацией. До выполнения этого пути держать endpoint compatibility-only, не обещать canonical replay. Нужны persistence schema/Alembic для detached-turn/event binding и web API/client integration.

### R-13 [High] Gateway M06 оставляет несколько evidence write неатомарными

`migration/m06_grants_gateway/adapter.py:238-252`, `:254-277`, `:283-305` по отдельности пишет mapping, quarantine, shadow и audit. Если поздняя операция падает, частичная запись может выглядеть завершённой; особенно mapped record уже существует до parity/audit. Противоречие также подтверждено `adapter-cross-domain-review.md:57-63` и `audit-append-shape-decision.md:181-202`.

**Эффект:** promotion/parity может опираться на evidence без связанного audit/quarantine или на audit без того snapshot, который породил решение.

**Remediation:** одна trusted transaction/evidence envelope должна связать mapping+decision+shadow+quarantine/audit под одинаковыми partition/source/binding digests; append-only IDs и DB uniqueness, audit failure rollback. До этого не учитывать такую запись в readiness. Требуются persistence adapter, ORM/Alembic/RLS/privileges.

## Cross-cutting disposition

- **Persistence/Alembic required:** M04 resource source mapping+versions/bindings; atomic owner/scope evidence and RLS; M05 exact composite instance key, reciprocal scope FK, runtime/writer/lease generations; M06 canonical grant/mapping/quarantine/audit tables, uniqueness and atomic evidence/cutover transaction; equipment tenant/project anchors, association tables and same-request/selected-membership invariant; automation task/outbox/delivery/idempotency rows and lease CAS; web detached-turn/run/event index. Models are source of truth, then reviewed linear Alembic chain; no manual DDL or shared-env migration is implied.
- **Web integration required:** complete SSE parser and envelope validation, server scope checks before legacy service, explicit session/cursor propagation, durable turn reattach and run-aware replay; client must not advance cursor before successful event handling.
- **Application/security required:** duplicate capability-name rejection, source expiry classification, SSRF-safe delivery transport, sanitized errors, explicit partial resource materialization policy, and no side-effect authorization based only on a prior evidence read.
- **Promotion posture:** inspected M04/M05/M06 promotion records state blocked/no admitted partition and `canonical_positive_writes=false`; keep those gates closed. A static `ready` report or unit-level protocol cannot substitute for persisted proof, DB constraints/RLS, exact partition lineage, and a reviewed owner-transition record.

## Follow-up disposition (2026-11-01)

The following application-level contracts were added after the baseline review. They reduce the cited code-path risks but do not establish deployed persistence, network enforcement, or production wiring:

- **R-01/R-02: addressed in the M06 shadow adapter contract.** `apps/api/src/prodavan/application/migration/m06_grants_gateway/adapter.py` rejects duplicate capability entries, validates source grant lifetime at a fixed aware timestamp, and routes mapping/quarantine/shadow/audit through one `TrustedEvidenceTransactionPort`. The repository/schema/RLS adapter is absent; M06 promotion remains blocked.
- **R-03/R-04: contract addressed in MIG-RES-01.** `apps/api/src/prodavan/application/migration/resources/resources.py` recomputes a digest across source envelope, identity, versions, and binding; import uses one trusted identity+digest CAS port and resolution requires one immutable scope/version snapshot with generation. Durable adapter, RLS/FKs and apply-time generation CAS are not implemented.
- **R-05: contract addressed.** `apps/api/src/prodavan/application/equipment_evidence_handoff/service.py` requires a trusted atomic admission port for each intent; `contracts.py` requires exact candidate/offer membership proof for `VERIFIED`. No persistence adapter or deployed membership constraints exist.
- **R-06: application contract addressed.** `apps/api/src/prodavan/application/resource_materialization/contracts.py` binds full selection snapshot and operation-set digests to generation; `service.py` blocks all incomplete resolutions and delegates apply only to `AtomicMaterializationApplyPort`. No adapter exists, so apply remains blocked.
- **R-07: application contract addressed.** `apps/api/src/prodavan/application/migration/m06_cutover/cutover.py` uses one atomic repository `transition` contract, partition-bound typed gate evidence and a mandatory verifier before promotion; absent verifier blocks. No durable repository/lease integration exists.
- **R-08/R-09: application fencing contracts added, integration remains open.** `apps/api/src/prodavan/application/automation_persistence/contracts.py` defines token+generation CAS and blocked persistence default; `automation_service/service.py` now passes claims into CAS lifecycle methods and stores stable error class/code digests rather than raw exception text. **Residual risk:** service and persistence packages still define duplicate `LeaseToken`/`DurableError` shapes, and legacy `apps/api/tests/unit/test_automation_service.py` fakes/assertions still use mutable records and old lifecycle calls. No concrete DB adapter means fencing is not operational.
- **R-10: fail-closed policy contract added, concrete enforcement open.** `apps/api/src/prodavan/application/automation_service/contracts.py` models scheme, credentials, port, resolved IP, redirect-hop and TLS proofs; `AutomationService.deliver()` denies if policy is missing or any proof is false. No production DNS/HTTP transport implementation proves those fields, and `OutboxWorker` policy wiring has not been established as deployed enforcement.
- **R-11: client parser/cursor contract addressed.** `apps/web/src/api/client.ts` parses CR/LF/CRLF, multiline data, comments, id/event and EOF; validates envelopes, commits cursor only after the handler, suppresses committed duplicates and cancels the reader on errors. Tests are authored, not run.
- **R-12: client now requires a durable binding, server remains blocked.** `runChat` requires tenant/project/session/turn/run binding and cursor, and never retries an unknown POST outcome. Backend facade lacks the durable run/event binding and run-aware replay integration; canonical replay is not enabled.
- **R-13: M06 evidence writes use an atomic transaction contract.** Mapping, quarantine, shadow and audit are submitted as one `EvidenceTransaction`; without a concrete persistence implementation and DB atomicity proof, this remains a contract rather than a verified transaction.

All above migration promotion gates remain blocked. T00 registers M04/M05 ORM modules but intentionally does not register `canonical_grants`: it conflicts with the already registered legacy `dynamic_app_capability_grants` metadata table. No Alembic chain/RLS deployment exists. These are release blockers, not follow-up cleanups that can be inferred away.

## Scope and verification

This is a static risk review, not proof that all cited code paths are wired in production. Findings distinguish application contract improvements from absent persistence, network enforcement and backend web integration; blocked migration paths must remain disabled. No tests, builds, lint, format, compilation, Alembic/migration execution, environment changes or git checks were run. No commit or push was performed.
