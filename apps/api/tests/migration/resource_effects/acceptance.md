# MIG-RES-EFFECT-01 acceptance artifact — `ResourceOperationEffectPort`

Pod/workspace implementation of the resource-effect boundary. **Artifacts only** —
nothing here was executed during the migration verification freeze; every claim below
is a checkable statement about code and contracts, not a record of a passed run.

## 1. Gap this task closes

`DurableMaterializationExecutor.execute` refuses a **populated** plan when
`effects is None`:

```
if plan.operations and self.effects is None:
    return _blocked("resource_effect_port_missing")
```

Nothing implemented `ResourceOperationEffectPort`, so every resource apply that had
operations to write answered `BLOCKED resource_effect_port_missing`; only empty plans
exercised the fence/attempt/audit path. `workspace_effects.py` supplies the missing
adapter over the **existing** Pod workspace runtime — no new transport, no new store,
no in-memory stub.

## 2. What is implemented

| Surface | Implementation | Evidence |
|---|---|---|
| `apply_operation` for `file`/`prompt` | `PodWorkspacePort.write_bytes` with the project's live `runtime_ref` | `test_prompt_operation_writes_utf8_text_through_the_pod_workspace_port`, `test_file_operation_writes_*` |
| `env` / `mcp` / `scope` | **not** implemented: no workspace write location exists in this codebase for them (env files and MCP/scope state belong to the Pod runtime materialization zone), so they answer `resource_effect_<kind>_unsupported` | `test_env_mcp_and_scope_are_refused_with_named_codes`, `test_unsupported_reason_code_is_derived_from_the_kind` |
| `compensate_operation` | idempotent delete of what was written under the same fence | `test_compensation_deletes_the_written_file`, `test_compensation_is_idempotent`, `test_a_later_write_after_compensation_can_be_compensated_again` |
| `apply_rollback` | typed refusal (`resource_effect_rollback_unsupported`) that the ledger cannot mistake for success | `test_rollback_is_a_typed_refusal_the_ledger_cannot_read_as_success` |
| Fencing | receipt echoes the exact `fence_token`/`fence_generation`; `operation_digest = resource_operation_digest(operation)`; a fence minted for another resource is refused | `test_receipt_echoes_the_exact_fence_and_operation_digest`, `test_fence_minted_for_another_resource_is_refused_and_nothing_is_written` |
| Path safety | relative, canonical, inside `/workspace`; absolute/`..`/empty-segment/NUL/percent-encoded traversal refused before any Pod call | `test_traversal_targets_are_refused_and_nothing_is_written`, `test_absolute_targets_are_refused`, `test_nul_control_and_blank_targets_are_refused` |
| Idempotency | repeat under the same fence returns the stored receipt and writes nothing | `test_repeat_under_the_same_fence_is_a_no_op_with_the_same_receipt` |
| Fail-closed runtime | stub runtime, non-`running` observation, blank or `object-ws:` ref, unreadable runtime store ⇒ refusal, no write | `test_pod_not_running_refuses_without_writing`, `test_unreadable_runtime_is_a_refusal_not_an_exception`, `test_running_runtime_ref_applies_the_runtime_guard_predicates` |
| Secrets | logs carry kind, reason code, generation, byte count only | `test_no_payload_path_or_secret_reaches_the_log`, `test_refusals_log_only_the_kind_and_reason_code` |

Payload decoding (documented, so a planner author knows what lands on disk):

* `prompt` → the first of `body_md`, `body`, `content`, `text` encoded as UTF-8. A
  prompt that only carries `prompt_fragments`/`files` is **refused**
  (`resource_effect_prompt_payload_unsupported`): composing fragments is an assembly
  decision this adapter must not guess. No inline body at all ⇒
  `resource_effect_prompt_payload_missing`.
* `file` → inline text (same keys), else `data_base64`, else the bytes of the immutable
  asset named by `file_ref`/`asset_ref`/`storage_key`, read through the existing
  `FileStoreManager`. A named-but-unlocatable asset is refused
  (`resource_effect_asset_unreadable`) rather than written empty.
* Both are bounded by the same 10 MiB ceiling `PodWorkspaceService.write_content`
  enforces (`resource_effect_payload_too_large`).

## 3. Architectural decisions and why

1. **Reuse `PodWorkspacePort`, not a new filesystem layer.** The port is exactly what
   `PodWorkspaceService` uses, so the resource apply writes through the same
   k8s-exec / agent-runtime HTTP / sandbox HTTP adapter the workspace API uses.
   Parent-directory creation is part of that write contract
   (`runtime/workspace_fs.py::cmd_write` stages a temp file in the destination
   directory and `os.replace`s it), so a nested target such as `prompts/x.md` needs no
   separate `create_directory` call here.
2. **Re-implement the runtime *predicate*, not `require_running_pod_runtime`.** The
   guard needs a `Principal` + `EmployeeRow` to check project access; a ledger-driven
   effect has no end user. `running_runtime_ref()` therefore applies the guard's
   *runtime* predicates verbatim (`stub` ⇒ refuse unless in-process adapters,
   `observed_state != "running"` ⇒ refuse, empty/`object-ws:` ref ⇒ refuse) and the
   access check stays where it belongs: the HTTP command that triggers the apply.
   Nothing degrades: an unresolvable runtime is a refusal, never a silent write.
3. **Fence is verified, never minted.** The adapter does not create, renew or
   increment a fence; it proves `fence.resource_key == resource_key_for_project(project_id)`
   and echoes the token/generation unchanged, so the ledger's own
   `OperationEffectReceipt.matches` is the single comparison. A receipt can therefore
   never "helpfully" correct a stale fence.
4. **`env`/`mcp`/`scope` refuse instead of pretending.** There is no env-file writer,
   MCP-config writer or scope-registry writer reachable from a resource apply in this
   tree (`resource_materialization/service.py` maps `ResourceKind.MCP → OperationKind.MCP`,
   and the resolver's `_target()` suggests `mcp/<slug>.json`, but no code writes it).
   Inventing one here would write a second, competing MCP layout. The named code keeps
   the gap visible and lets a plan author see exactly which kind failed.
5. **Rollback is refused, and refused in a way the ledger cannot misread.** No
   workspace-byte snapshot is persisted for a revision, so "restore generation N" is
   not performable. See §5 for why the refusal had to be shaped around an executor
   defect.
6. **Replay memory is per (project, resource key, generation, fence token, digest).**
   Fence-scoped, so generation 8 writing the same bytes is not answered from
   generation 7's receipt. Bounded (2048) and eviction is harmless: a forgotten
   receipt only means a repeat rewrites identical bytes (idempotent by content) and a
   compensation refuses (`resource_effect_compensation_unrecorded`) instead of deleting
   a path it cannot prove it wrote. Cross-process idempotency remains the ledger's job
   (replay is resolved before the CAS, so a second worker never reaches the adapter).

## 4. Negative and boundary coverage

* traversal (`../escape.md`, `prompts/../../escape.md`, `a/../../b`, `prompts/./x`,
  `prompts//x`, `prompts/`, `%2e%2e%2fescape.md`, `%252e%252e%252f…`,
  `prompts\..\..\escape.md`) ⇒ `resource_effect_target_unsafe`, **and neither a write
  nor a runtime lookup happened**;
* absolute targets (`/etc/passwd`, `/workspace/x`, `\x`, `C:/x`) ⇒
  `resource_effect_target_absolute`;
* NUL/newline in a target ⇒ `resource_effect_target_unsafe`; blank target ⇒
  `resource_effect_target_missing` (asserted on the path function, because
  `ResourceOperation` itself rejects an empty target);
* over-long target (> 500 chars, the workspace API's own bound) ⇒
  `resource_effect_target_too_long`;
* a fence minted for another project's resource ⇒
  `resource_effect_fence_resource_mismatch` on both apply and compensation;
* a receipt for a different fence token or generation does not `matches()`;
* compensation with no recorded write refuses and leaves a foreign file alone;
  compensation of a directory refuses; a failed delete is `effect_unknown=True`;
* a write that fails **after** the bytes landed is still `effect_unknown=True` (the
  worst case: the file exists and the ledger must not believe it did not);
* an oversized payload is refused *before* the runtime is even resolved;
* every reason code is validated against the ledger's own
  `RedactedDiagnostic` (shape `^[a-z][a-z0-9_.-]{0,63}$`, no credential markers) so no
  refusal can be dropped by `_safe_code`/`_require_code` on the ledger side;
* `ResourceEffectRefused.__str__` is the reason code alone — an accidental
  `logger.exception` upstream cannot leak a path or a payload through the message.

## 5. Recorded defects and blockers outside this task's zone

1. **Executor rollback branch ignores `succeeded`.**
   `executor._rollback_in_transaction` accepts a rollback receipt on
   `_receipt_matches(...)` alone (`executor.py`, "if receipt is None or not
   _receipt_matches(...)"). A conforming adapter that answered
   `succeeded=False, operation_digest=<expected>` would be recorded as a **completed
   rollback**. Until the executor also tests `receipt.succeeded`, the honest refusal
   has to be shaped as a digest mismatch (`_rollback_refusal_digest`), which makes the
   ledger answer `FAILED rollback_effect_unknown`. Owner: executor zone.
   Do not "fix" this by returning a matching receipt from `workspace_effects.py`.
2. **Wiring is not done here (file zone).** Two one-line changes are needed outside
   this task's zone, both already supported by existing seams:
   `core/infra/resource_apply_resource.py` (or whoever calls
   `build_resource_materialization_apply_port`) must pass
   `effects=build_pod_workspace_effect_port(session_factory=…)`; and the
   `"resource_effect"` entry of `EVIDENCE_AUTHORITY_BLOCKERS` in `apply_adapter.py`
   ("no Pod/workspace effect adapter implements ResourceOperationEffectPort") is now
   stale and should be removed or reworded. Until the wiring lands, behaviour is
   unchanged: `BLOCKED resource_effect_port_missing`.
3. **`durable/__init__.py` does not export the new port** (outside the zone). Import
   it from
   `prodavan.application.resource_materialization.durable.workspace_effects` directly,
   or add it to the package surface in the parent task.
4. **`env`/`mcp`/`scope` remain unimplemented** by design (§3.4). A plan containing
   them as `required=True` will now fail with the named code instead of
   `resource_effect_port_missing`; the outcome class is the same (`FAILED`,
   `required_operation_failed`), the diagnosis is exact.
5. **Compensation is process-local.** A crash between write and compensation loses the
   receipt, so the retry refuses (`resource_effect_compensation_unrecorded`) instead of
   deleting. That is the fail-closed choice: a dangling file is recoverable, deleting
   an unproven path is not. Making it crash-safe needs a durable receipt column, which
   is a models/Alembic change outside this zone.

## 6. Files

* `apps/api/src/prodavan/application/resource_materialization/durable/workspace_effects.py` (new)
* `apps/api/tests/migration/resource_effects/__init__.py` (new)
* `apps/api/tests/migration/resource_effects/doubles.py` (new, test tree only)
* `apps/api/tests/migration/resource_effects/test_workspace_effects.py` (new)
* `apps/api/tests/migration/resource_effects/acceptance.md` (this file)

Untouched: `durable/effects.py` (contract), `durable/executor.py`, `models/**`,
Alembic, `application/pod_service/**` (read-only), `apply_adapter.py`.

Formal tests/build/lint/format/compile/git checks are intentionally not run per the
verification freeze. `ast.parse` on the new module and an import-only
`python -c` (`import prodavan.application.resource_materialization.durable.workspace_effects`)
were used as syntax checks; no test was executed.
