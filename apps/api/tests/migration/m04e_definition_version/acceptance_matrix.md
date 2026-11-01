# MIG-M04E-02 acceptance matrix

Status: authored, not executed (migration verification freeze). Scope is limited to the task-owned zone.

| Case | Expected contract result | Evidence location |
|---|---|---|
| Bounded read | At most configured page size (1..1000); source IDs deterministically ordered | `DefinitionVersionEvidenceBackfill.run_page` |
| Resume after interruption | Checkpoint resumes after last source ID and sequence; completed definition phase advances to version phase | `BackfillCheckpoint` |
| Exact source replay | Existing source/schema and digest returns original immutable evidence | `EvidenceStore.find` + coordinator |
| Changed source digest | Record conflict/quarantine; prior evidence is not overwritten | `source_digest_changed` |
| Dependency gate missing | No source read; blocked/quarantined marker and unchanged checkpoint | `dependency_gate_missing` |
| Version before definition phase complete | Not scheduled; reader receives only current checkpoint phase | coordinator |
| Missing tenant/definition/artifact | Quarantine, no canonical digest | `_candidate_is_provable` |
| Unsupported lifecycle/current-version inconsistency | Quarantine at resolver; no positive reference | existing M04.e resolver contract |
| Caller tenant mismatch | Fail closed; no resolver call | `ServerDefinitionVersionRepository.resolve` |
| Version ID without definition proof | No public method accepts version ID alone | server facade surface |
| M04 or M04E-01 missing | M05 export blocked; handoff cannot be used | `promotion_record.json` |
| Empty quarantine and all gates | Handoff may be marked ready for evidence consumption only; positive writes remain false | `export_m05_evidence` |

## Required review gates

- Confirm immutable evidence store append semantics and unique key `(source_kind, source_id, schema_version)` before wiring a durable adapter.
- Confirm active typed parent scope and retained tenant resolution are supplied by authenticated server context.
- Confirm definition/version constraints and lifecycle proof are established by MIG-M04E-01.
- Validate artifact digest equality and current-version link against persisted history; no source data or shared database was available in this task context.
- M05 must consume only a digest-linked export with all upstream gate records; this task does not authorize instance publication or grants.
