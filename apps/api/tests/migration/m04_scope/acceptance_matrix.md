# MIG-M04-02 acceptance matrix

Status: authored for review; deliberately not executed under migration verification freeze.

| Case | Expected result | Acceptance artifact |
|---|---|---|
| Deterministic typed hierarchy | tenant scope ID = tenant ID; cabinet/project/plugin scope ID equals its typed anchor; malformed parent shape fails before persistence | `ScopeMappingInput.to_anchors`; mapping fixtures |
| Source mapping idempotency | source key is `(source_kind, source_id, source_schema_version)` per M04-01; same digest, schema and anchors replays first outcome, mapping and audit IDs without advancing checkpoint | `assess_mapping_replay`; trusted transaction port contract |
| Changed source digest | conflict/quarantine preserves first mapping; append redacted audit; commit resumable cursor for that observed source row | coordinator result contract; port CAS contract |
| Changed source schema | distinct M04-01 source key; no accidental overwrite or replay of a prior schema observation | mapping identity acceptance cases |
| Changed canonical schema/identity | conflict/quarantine; preserve first mapping; never reassign identity | `assess_mapping_replay`; trusted transaction port contract |
| Snapshot/checkpoint resume | resume exact tenant/stream/snapshot/source schema/canonical schema/digest; another digest or stale sequence fails without resetting stored cursor | `ScopeStreamIdentity`, `ScopeCheckpointPort`, `resume_checkpoint` |
| Deleted or archived tenant/cabinet/project | classify with stable reason and quarantine; only retained parents without soft-delete or archival qualify | `ScopeParentEvidence.invalid_reason_for` cases |
| Missing or ambiguous company/cabinet membership | deny/quarantine; exact single active `cabinet_company_grants(cabinet_id, company_id)` required; employee assignments and `company_grant_scope=all` cannot substitute as tenant membership evidence | parent evidence tests; M04-01 membership blocker |
| Company and employee membership | `memberships` has unique `(company_id, employee_id)` but no lifecycle/status field; require a current employee/company access transaction and do not treat membership existence alone as cabinet grant | actual access source inventory; port contract |
| Project hierarchy | exact live `projects(id, company_id, cabinet_id)` tuple, matching tenant/cabinet, with no deletion; project runtime pause is not conflated with owner activation | project-parent evidence tests |
| Cross-tenant or caller-supplied tenant | caller assertions are comparison-only; mismatches quarantine mapping and deny resolution | source assertion and exact-context cases |
| Archived/quarantined typed scope | never returned active | active scope evaluator cases |
| Missing transaction context or RLS evidence | deny; require same transaction, exact tenant/cabinet/project GUC, ENABLE+FORCE, USING+WITH CHECK, missing-context-deny, non-BYPASSRLS role and reviewed typed-parent policy | `ScopeRlsEvidence.valid_for`; resolver fail-closed cases |
| Missing/ambiguous actor tenant membership | deny; require exactly one current employee membership or exact company-principal binding for the authenticated actor in the same transaction | `ScopeActorMembershipEvidence`; resolver acceptance cases |
| Duplicate exact active scope | port returns no positive result unless cardinality is exactly one; ambiguity quarantines or denies | active registry cardinality fields and trusted port contract |
| Plugin instance | explicit deny/defer; no positive branch in M04 | plugin mapping and resolution cases |
| Raw source payload / credentials | quarantine and audit retain ID, digest, reason, checkpoint and correlation only; no raw payload | source/quarantine DTOs |
| Source ID over typed provenance width | retain exact ID up to owner-mapping width 128 in redacted quarantine; reject larger ID without truncation | source width cases |
| Adapter/runtime verification | no concrete port exists; test scenarios do not simulate a successful persistence or migration | implementation boundary below |

## Actual access-source inspection

`CabinetGrantService` and `RelationsQuery` resolve the active `cabinet_company_grants` rows, and cabinet access also supports employee assignments and `company_grant_scope=all`. `ProjectAccessPolicy` checks soft-deletion, cabinet access and (for restricted projects) project assignments. `EntitlementService` treats membership as a unique company/employee relation; its model has no status/deleted field. Project ORM rows carry independent company and cabinet FKs, while M04-01 supplies a composite project tuple key. A mapping adapter must select the intended semantics for tenant-to-cabinet admission and membership identity; this task has no source snapshot/actor/work transaction to establish it. Never infer one active relation from `CabinetInstance.company_id`, `owner_company_id`, grant-scope `all`, or independent project FKs.

## Safety and implementation boundary

Mapping/checkpoint/audit writes remain one atomic trusted-port contract; no persistence adapter, checkpoint storage, RLS installer or active resolver binding is present. `ScopeMappingInput` and source assertions are untrusted data. Parent rows, active grants, project tuple and active registry cardinalities must be re-derived inside the same trusted transaction. `ScopeRlsEvidence` is required as a returned reviewed attestation, but its booleans are not proof unless a production adapter derives them from installed schema/role/policy state. Legacy remains the sole owner. No routes, DDL, live data, cutover, environment changes or positive writes are introduced. Tests, builds, lint, compile, format and git checks were intentionally not run.
