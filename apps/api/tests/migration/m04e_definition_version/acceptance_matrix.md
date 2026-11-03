# MIG-M04E-02 acceptance matrix

Status: authored, not executed (migration verification freeze). Scope is limited to the task-owned zone.

| Case | Expected contract result | Evidence location |
|---|---|---|
| Bounded read | At most configured page size (1..1000); source IDs deterministically ordered | `DefinitionVersionEvidenceBackfill.run_page` |
| Resume after interruption | Checkpoint resumes after last source ID and sequence; completed definition phase advances to version phase | `BackfillCheckpoint` |
| Exact source and verified artifact replay | Returns first immutable evidence and durable cursor; does not append or advance | `EvidenceStore.classify_and_commit_page` |
| Exact conflict-observation replay | Returns the first conflict observation and unchanged cursor for the same changed-source/artifact digest | `EvidenceStore.find_observation` |
| Changed source or artifact digest | Conflict retains first source/canonical mapping, records observed digest and never overwrites | `observed_source_digest` / `observed_artifact_digest` |
| Quarantine diagnosis and exact replay | Reason-specific redacted repair guidance is stable across replay; original digest, quarantine count and checkpoint remain unchanged. Repaired-row retry is blocked until a durable retry operation is specified. | `test_missing_artifact_quarantine_replay_preserves_operator_guidance_counts_and_cursor` |
| Dependency gate missing | No source read; blocked marker carries concrete blockers and unchanged checkpoint | `UPSTREAM_BLOCKERS` |
| Version before definition phase complete | Not scheduled; reader receives only current checkpoint phase | coordinator |
| Missing tenant/definition/artifact bytes | Quarantine, no canonical digest | `test_behavioral_acceptance.py` |
| Artifact bytes differ from source/canonical digest | Quarantine or immutable conflict; no positive reference | `_verify_artifact` |
| Archived lifecycle/current-version ambiguity or mismatch | Deny/quarantine; no positive reference | `ServerDefinitionVersionRepository.resolve` |
| Caller tenant mismatch | Fail closed; no resolver call | `ServerDefinitionVersionRepository.resolve` |
| Version ID without definition proof | No public method accepts version ID alone | server facade surface |
| M04 or M04E-01 missing | M05 export blocked; only explicit blocked handoff marker allowed | `promotion_record_v2.json` |
| Handoff marker | Explicit ready/blocked evidence marker only after every gate; positive writes remain false | `persist_m05_handoff` |

## Required review gates

- Confirm immutable evidence store append semantics and unique key `(source_kind, source_id, schema_version)` before wiring a durable adapter.
- Confirm active typed parent scope and retained tenant resolution are supplied by authenticated server context.
- Confirm definition/version constraints and lifecycle proof are established by MIG-M04E-01.
- Bind a production artifact content reader and re-prove exact version URI/digest; no production reader exists in this task zone.
- Provide database-enforced immutable artifact proof, exactly-one current-version proof, and durable CAS evidence writer before any candidate reads are enabled.
- Acquire a deterministic bounded source snapshot with tenant/definition lineage and audited counts; none was supplied in this task.
- M05 must consume only a digest-linked export with all upstream gate records; this task does not authorize instance publication or grants.

## Behavioral fixtures (not executed)

`test_behavioral_acceptance.py` exercises replay and changed-digest conflict, artifact mismatch/missing with stable quarantine diagnosis and counts, archived evidence/status, bad current-version linkage, caller tenant mismatch, blocked upstream behavior, and explicit M05 marker persistence. `acceptance_fixtures.py` supplies fixture-only port implementations; these are not durable adapters and are not production evidence. There is no persistence-backed repaired-quarantine retry fixture because the store contract has no retry operation; that recovery remains blocked.
