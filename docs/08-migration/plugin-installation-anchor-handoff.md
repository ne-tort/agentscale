# Plugin installation anchor handoff

Статус: migration-stage contract, только application contracts/ports/service/tests и документация. Baseline: 2026-11-01.

Документ закрывает cross-domain mismatch между plugin installation admission, M05 runtime CAS и M06 grant authorization. Он задаёт один exact immutable context и порядок handoff. В этой волне не добавляются ORM-модели, записи, Alembic, routes или persistence adapters.

## 1. Canonical context

`PluginInstallationContext` является server-resolved authority для одного installation/runtime call. Client, browser, plugin, payload и `company_id` могут передавать только assertions; они сравниваются с этим context и не подменяют его.

| Anchor | Required meaning |
|---|---|
| `tenant_id` | Canonical retained tenant (`companies.id`); never inferred from plugin or request |
| `cabinet_id` | Exact cabinet installation parent, nullable only for an approved tenant/project policy |
| `project_id` | Exact project parent; project scope requires both project and cabinet compatibility |
| `scope_kind` | Exactly `tenant`, `cabinet`, `project` or `plugin_instance` |
| `scope_id` | Typed scope identity: tenant/cabinet/project ID, or exact plugin instance ID |
| `plugin_instance_id` | Exact immutable installation identity; never app/version/scope/session substitute |
| `definition_id` | Exact app/plugin definition owner |
| `version_id` | Exact definition version |
| `artifact_digest` | Immutable published artifact digest, `sha256:` form |
| `subject_id` | Server-resolved subject binding |
| `session_id` | Exact active runtime/session binding; never installation identity |
| `runtime_generation` | M05 restart/redeploy generation |
| `writer_generation` | M05 CAS writer epoch |
| `lease_owner`, `lease_id` | Current runtime lease/fencing evidence |
| `observed_at` | Timezone-aware evidence timestamp |

Typed shape invariants are fail-closed:

- `tenant`: `scope_id == tenant_id`; cabinet/project/plugin-instance anchors are absent.
- `cabinet`: `scope_id == cabinet_id`; project/plugin-instance anchors are absent, and tenant/cabinet compatibility is required.
- `project`: `scope_id == project_id`; tenant and cabinet compatibility are required, and the plugin-instance anchor is absent.
- `plugin_instance`: `scope_id == plugin_instance_id`; installation cabinet/project anchors mirror the exact installation scope, a project anchor requires a cabinet anchor, and both are absent only under an approved tenant installation policy.

The exact M05 identity key is:

```text
(plugin_instance_id, definition_id, version_id, tenant_id, scope_kind, scope_id)
```

No fallback to another instance, app-wide grant, session-as-instance, or guessed parent is allowed.

## 2. Admission sequence

`PluginInstallationAdmissionService` is read/admission-only and performs the following sequence:

1. Reject blank or structurally invalid context at construction time.
2. Compare optional client/plugin assertions (`tenant`, `cabinet`, `project`, `plugin_instance`) with the supplied context.
3. Resolve the authoritative context by exact `plugin_instance_id + session_id`.
4. Require byte-for-byte equality of all anchors, definition/version/artifact, subject/session and fencing generations between supplied and resolved context.
5. Require active installation/registry/lifecycle evidence through `InstallationContextConsistencyPort`.
6. Build `M05CasContext` from the resolved context and call runtime admission.
7. On successful M05 admission, build `M06GrantHandoff` from the same context and optionally notify the M06 boundary.
8. Return no M05 or M06 handoff on any deny, mismatch, inactive, missing or stale-fence result.

A denied result is one of `installation_not_found`, `anchor_mismatch`, `installation_inactive`, `runtime_fence_denied` or `invalid_context`. It never becomes a grant allow through legacy JSON, requested capabilities or a second resolution attempt.

## 3. M05 CAS handoff

`M05CasContext` contains only the exact `PluginInstallationContext`. The M05 port must use these values for its existing exact-key and CAS checks:

- identity tuple: tenant, scope kind/id, definition/version, exact plugin instance and artifact;
- subject/session equality;
- current `runtime_generation`;
- current `writer_generation`;
- lease owner and lease ID;
- active lifecycle and unexpired lease.

M05 admission is not a grant. A successful CAS/runtime decision only proves that this exact installation and worker may continue to the M06 boundary. Stale generations, stale lease, archived/disabled/quarantined installation, artifact mismatch and cross-parent identity all deny or quarantine.

## 4. M06 grant handoff

`M06GrantHandoff` contains the same immutable context plus `runtime_admitted=True`. M06 must not independently reconstruct tenant, scope, installation, subject or session from caller assertions. It resolves grant candidates using the exact handoff and still applies capability, operation, resource, domain, lifecycle, expiry, revoke, artifact and grant-generation policy.

For a concrete plugin/runtime call, M06 requires:

- exact tenant and typed scope registry resolution;
- same definition/version and artifact digest;
- exact `plugin_instance_id` relation;
- exact subject and session binding;
- current runtime/writer generation and lease evidence from M05;
- canonical bounded capability data and no wildcard fallback.

M05 success never authorizes M06 by itself, and M06 grant presence never bypasses M05 runtime admission.

## 5. Port and implementation boundary

The new package is intentionally exclusive to:

```text
application/plugin_installation_context/
  contracts.py
  ports.py
  service.py
  __init__.py
apps/api/tests/unit/test_plugin_installation_context.py
```

Ports are read/admission protocols only. They expose no ORM session, SQL, `add`, `update`, `delete`, transaction writer, Alembic hook or HTTP route. Persistence implementations, registry FK closure and positive writes remain later migration slices. This contract does not authorize shared-environment DDL or cutover.

## 6. Acceptance evidence

- Every supported scope shape rejects a mismatched `scope_id`.
- Client/plugin anchor mismatch is denied before M05.
- Resolved context mismatch is denied before M05.
- Inactive installation and failed/stale runtime admission never reach M06.
- Successful admission passes the identical context object through M05 and M06.
- M06 receives no context when M05 denies.
- Direct construction of an M06 handoff with `runtime_admitted=False` is rejected.
- No contract exposes a mutation method or persistence dependency.

Tests/build/lint/format/compile/Alembic execution and git checks are intentionally not run due to the migration verification freeze.
