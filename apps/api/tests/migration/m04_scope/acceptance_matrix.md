# MIG-M04-02 acceptance matrix

Status: authored for review; deliberately not executed under migration verification freeze.

| Case | Expected result | Artifact |
|---|---|---|
| Single source row | One transaction checks source digest, derives parent proof, appends mapping/quarantine/audit/checkpoint atomically | `TrustedScopeTransactionPort` |
| Exact source/schema/digest/typed-identity replay | Original mapping and checkpoint returned; no duplicate audit/registry identity | `test_exact_replay_returns_original_mapping_without_duplicate_audit` |
| Changed digest | Conflict and durable quarantine; first mapping remains unchanged | `test_changed_digest_conflicts_without_overwriting_first_mapping` |
| Checkpoint CAS | Unseen source row with stale sequence is blocked with no evidence mutation; source cursor ordering belongs to source reader | `test_checkpoint_compare_and_swap_blocks_stale_unseen_row` |
| Missing/deleted tenant/cabinet/project | Quarantine; no active registry entry | parent-proof test fixtures |
| Missing or ambiguous active cabinet membership | Quarantine; no active registry entry | `test_missing_membership_or_project_compatibility_quarantines` |
| Project/company/cabinet mismatch | Quarantine even if individual parent rows exist | compatibility proof fixture |
| Cross-tenant source assertion | Quarantine and redacted audit reason | `test_cross_tenant_assertion_is_quarantined` |
| Archived/quarantined registry state | Excluded from active resolver | `test_resolver_excludes_archived_and_quarantined_rows` |
| Missing/malformed tenant context or persistence port | Block/deny before any persistence claim | missing-context/port tests |
| Wrong cabinet/project in transaction-local context | Deny before repository resolution | `test_resolver_denies_context_for_different_cabinet_or_project` |
| Duplicate logical scope from another source | Quarantine rather than create second active registry identity | duplicate assertion test |
| Plugin instance scope | Deferred/quarantined, never active until M05 exact identity and reciprocal FK gate | `test_plugin_instance_is_always_deferred_until_m05` |
| RLS no-context/cross-tenant | Deny; persisted policy review required before readiness | M04-01 RLS review and promotion gate |
| Raw secret/token/payload leakage | Quarantine/audit carry IDs, digests, reason and correlation only; raw payload excluded | redacted audit assertions |
| Resume after committed row | Checkpoint advances once per unseen row; exact replay does not duplicate audit | replay and checkpoint CAS acceptance tests |
| Returned evidence mismatch | Coordinator rejects source identity/schema/digest/correlation mismatch and positive-write claims | `ScopeMigrationCoordinator.process_row` contract |

## Safety/implementation boundary

The application layer has no concrete DB adapter in this task zone. Positive candidate persistence can only be attempted via a trusted transaction port whose contract requires transaction-local tenant context, source digest comparison, exact parent membership/tuple proof, and atomic append. With missing port/context/proof, the coordinator blocks or proposes quarantine and never reports a positive canonical write. Resolver returns only one exact active typed row with valid parent proof; plugin instance is always deferred. The in-memory fake is only test behavior, not evidence that PostgreSQL transaction or RLS semantics exist.
