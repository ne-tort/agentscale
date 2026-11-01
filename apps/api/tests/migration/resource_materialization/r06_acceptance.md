# R-06 acceptance artifact

Task-owned acceptance cases for resource materialization. These are artifacts only and are intentionally not executed during migration freeze.

- The resolution contains an immutable full-selection snapshot digest and snapshot generation; digest input includes project/revision/target scope, every selection, and desired generation.
- Resolution is complete only when there are no failures. An optional selection failure clears every operation and keeps `ready=false`; no partial operation set is admissible.
- `AtomicMaterializationApplyPort.apply_cas` is the only apply boundary. It receives expected generation, full snapshot digest, full operation-set digest, and the complete immutable operation tuple.
- `apply_resolution` returns `BLOCKED` when no persistence adapter is supplied, or when the resolution is incomplete. It never performs side effects itself.
- A trusted adapter must validate generation, snapshot digest, and the entire operation set before any side effect; CAS conflict returns a non-ready outcome.
- Partial operation failure or CAS conflict is never `ready` and cannot be reported as applied.

Formal tests/build/lint/format/compile/git checks are intentionally not run per verification freeze.
