# Migration Acceptance Matrix

Статус: рабочая матрица приемки migration stage.
Правило этапа: проверки и тесты проектируются, но не запускаются до завершения всей миграции и отдельного разрешения.

## 1. Platform and tenancy

| Инвариант | Доказательство в реализации | Обязательная проверка позже |
|---|---|---|
| tenant/cabinet/project scope не теряется | typed RequestContext + capability context | cross-tenant deny tests |
| plugin не получает shell token | isolated origin + opaque capability handle | browser token isolation test |
| каждый capability call повторно ACL-checks | gateway decision pipeline | replay/direct-call deny test |
| quotas enforce server-side | quota decision before adapter | rate/concurrency/bytes tests |
| audit не содержит secrets | redacted audit envelope | payload redaction test |

## 2. Dynamic UI

| Инвариант | Доказательство в реализации | Обязательная проверка позже |
|---|---|---|
| declarative app использует allowlisted components | typed app schema and registry | unknown component rejection |
| code plugin работает только на отдельном origin | iframe/CSP/host protocol | same-origin and cookie isolation |
| manifest не является grant | requested vs effective grant entities | capability denied without grant |
| RPC schema validated | versioned request/response envelopes | malformed/replay RPC tests |
| artifact immutable and rollbackable | digest/version/publish lifecycle | old-version rollback test |
| plugin cannot invoke generic infrastructure | no generic SQL/Redis/Kafka API | forbidden capability test |

## 3. Runtime revisions

| Инвариант | Доказательство в реализации | Обязательная проверка позже |
|---|---|---|
| prepared revision immutable | content-addressed bundle and revision rows | mutation rejection |
| apply is idempotent | revision/bundle idempotency key | repeated apply test |
| partial materialization is failed | fail-closed operation state | injected operation failure |
| desired/applied are explicit | project revision state | reconcile/retry test |
| prompt/MCP/file provenance exists | materialization entries and hashes | provenance projection test |
| bridge credential is not literal pod env | lease/projected secret design | pod spec secret inspection |
| image digest is pinned | runtime revision image digest | rollback/recreate test |

## 4. Resources

| Инвариант | Доказательство в реализации | Обязательная проверка позже |
|---|---|---|
| content blob is immutable | asset/blob version model | version immutability |
| project selection is explicit | resource project binding | scope resolution tests |
| profile revision is recorded by run | profile version reference | reproducibility test |
| MCP package has manifest and digest | package metadata and validator | invalid package rejection |
| secrets are references, not content | secret_ref/lease only | secret leakage scan |

## 5. Equipment

| Инвариант | Доказательство в реализации | Обязательная проверка позже |
|---|---|---|
| source import is reproducible | ingestion run/raw artifact/provenance | re-import fixture parity |
| supplier offers remain separate | supplier/source foreign keys | same-PN multi-supplier test |
| last-good stale offer remains visible | snapshot history/freshness state | source outage test |
| on_order is not silently in-stock | typed availability enum/policy | availability policy test |
| agent cannot author price | command separation and ACL | MCP write denial |
| OpenSearch is rebuildable | projection service from snapshots | drop/rebuild parity |
| tender lifecycle is separate | tender aggregate and commands | attach/readiness workflow |
| budget uses selected offers | procurement/budget references | calculation fixtures |

## 6. Agent operations

| Инвариant | Доказательство в реализации | Обязательная проверка позже |
|---|---|---|
| session != run | separate persistent entities | multi-run session test |
| events append-only and ordered | seq/idempotency envelope | duplicate/out-of-order event tests |
| approvals are durable | explicit state machine | expiry/reject/resume tests |
| webhook ingress is asynchronous | verify + enqueue only | retry/idempotency test |
| automation has quotas | task policy and leases | concurrency test |
| trace links tool/subagent/artifact | trace/run/event references | trace projection test |

## 7. Migration cutover

Cutover допускается только если:

- no new writes to legacy cabinet schema rows;
- no production-like reads through generic equipment JSONB rows;
- new web shell covers agreed core workflows;
- dynamic app/plugin ACL tests exist;
- runtime revision rollback path exists;
- equipment import snapshots and recovery path exist;
- compatibility facades have owners and removal dates;
- complete migration has been reviewed multiple times.
