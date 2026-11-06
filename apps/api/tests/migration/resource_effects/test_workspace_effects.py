"""MIG-RES-EFFECT-01 acceptance — Pod/workspace ``ResourceOperationEffectPort``.

Authored during the migration verification freeze: **not executed**. Every test is a
checkable claim about the adapter's behaviour against the published contracts, not a
record of a passed run.

Coverage map (the acceptance artifact required by the task):

=====================================================  ============================================
Claim                                                   Test
=====================================================  ============================================
``prompt``/``file`` are applied through the Pod port     ``test_prompt_operation_*``,
                                                        ``test_file_operation_*``
Path traversal (``../``, ``/abs``, ``a/../../b``) → no  ``test_traversal_targets_*``,
write                                                   ``test_absolute_*``, ``test_nul_*``,
                                                        ``test_percent_encoded_*``
Fence mismatch → refusal                                ``test_fence_minted_for_another_resource_*``
Receipt echoes the exact fence and operation digest      ``test_receipt_echoes_*``
Repeat under the same fence is a no-op, same receipt     ``test_repeat_under_the_same_fence_*``
Compensation deletes the write, and is idempotent        ``test_compensation_deletes_*``,
                                                        ``test_compensation_is_idempotent``
Unsupported kind → named refusal                        ``test_env_mcp_and_scope_*``
No secret/payload/path in the log                        ``test_no_payload_path_or_secret_*``
Runtime must be a running k8s sandbox                   ``test_pod_not_running_*``,
                                                        ``test_running_runtime_ref_*``
Rollback is a typed refusal the ledger cannot read as    ``test_rollback_is_a_typed_refusal_*``,
success                                                 ``test_rollback_support_flag_*``
Every reason code survives the ledger's code validation  ``test_every_reason_code_*``
=====================================================  ============================================

The doubles in ``doubles.py`` live only in the test tree; the production adapter
refuses with ``resource_effect_runtime_resolver_missing`` instead of inventing a Pod
(``test_missing_runtime_resolver_is_a_named_refusal``).
"""

from __future__ import annotations

import base64
import logging
import re

import pytest

from prodavan.application.materialization_adapter import ResourceOperation
from prodavan.application.migration.runtime_materialization.state_machine import (
    canonical_digest,
)
from prodavan.application.resource_materialization.durable.contracts import (
    RedactedDiagnostic,
    resource_key_for_project,
)
from prodavan.application.resource_materialization.durable.effects import (
    OperationEffectReceipt,
    OperationFence,
    resource_operation_digest,
)
from prodavan.application.resource_materialization.durable.workspace_effects import (
    REASON_APPLIED,
    REASON_ASSET_UNREADABLE,
    REASON_COMPENSATED,
    REASON_COMPENSATED_ABSENT,
    REASON_COMPENSATION_CONFLICT,
    REASON_COMPENSATION_UNKNOWN,
    REASON_COMPENSATION_UNRECORDED,
    REASON_FENCE_RESOURCE_MISMATCH,
    REASON_FILE_PAYLOAD_UNSUPPORTED,
    REASON_PAYLOAD_TOO_LARGE,
    REASON_PROJECT_SCOPE_INVALID,
    REASON_PROMPT_PAYLOAD_MISSING,
    REASON_PROMPT_PAYLOAD_UNSUPPORTED,
    REASON_ROLLBACK_UNSUPPORTED,
    REASON_RUNTIME_NOT_RUNNING,
    REASON_RUNTIME_RESOLVER_MISSING,
    REASON_RUNTIME_UNAVAILABLE,
    REASON_TARGET_ABSOLUTE,
    REASON_TARGET_MISSING,
    REASON_TARGET_TOO_LONG,
    REASON_TARGET_UNSAFE,
    REASON_WRITE_UNKNOWN,
    PodWorkspaceOperationEffectPort,
    ResourceEffectRefused,
    build_pod_workspace_effect_port,
    running_runtime_ref,
    unsupported_reason_code,
    workspace_relative_path,
)
from prodavan.domain.materialization import OperationKind, SourceProvenance

from . import doubles

PROJECT_ID = "prj_effect_01"
OTHER_PROJECT_ID = "prj_effect_02"
RUNTIME_REF = doubles.DEFAULT_RUNTIME_REF
PROMPT_TARGET = "prompts/apply-port.md"
FILE_TARGET = "files/report.csv"
PAYLOAD_TEXT = "materialized by the effect port"
SECRET_TEXT = "s3cr3t-do-not-log"

_CODE = re.compile(r"^[a-z][a-z0-9_.-]{0,63}$")


# --------------------------------------------------------------------------- #
# Fixtures and builders
# --------------------------------------------------------------------------- #


@pytest.fixture()
def workspace() -> doubles.RecordingWorkspace:
    return doubles.RecordingWorkspace()


@pytest.fixture()
def runtime() -> doubles.StaticRuntimeResolver:
    return doubles.StaticRuntimeResolver()


@pytest.fixture()
def assets() -> doubles.StaticAssets:
    return doubles.StaticAssets()


@pytest.fixture()
def port(
    workspace: doubles.RecordingWorkspace,
    runtime: doubles.StaticRuntimeResolver,
    assets: doubles.StaticAssets,
) -> PodWorkspaceOperationEffectPort:
    return PodWorkspaceOperationEffectPort(
        workspace=workspace, runtime=runtime, assets=assets
    )


def fence(
    *,
    project_id: str = PROJECT_ID,
    generation: int = 7,
    fence_token: str = "fence-token-7",
    resource_key: str | None = None,
) -> OperationFence:
    return OperationFence(
        resource_key=resource_key or resource_key_for_project(project_id),
        generation=generation,
        fence_token=fence_token,
    )


def provenance(target: str) -> SourceProvenance:
    return SourceProvenance(
        source_type="resource:prompt",
        source_id="res_effect_01",
        source_generation=4,
        source_path=target,
        source_revision="rver_effect_01_1",
        metadata={"binding_id": "rbind_effect_01", "version_number": 1},
    )


def operation(
    *,
    kind: OperationKind = OperationKind.PROMPT,
    target: str = PROMPT_TARGET,
    payload: dict | None = None,
    required: bool = True,
    with_provenance: bool = True,
) -> ResourceOperation:
    return ResourceOperation(
        kind=kind,
        target=target,
        payload={"body_md": PAYLOAD_TEXT} if payload is None else payload,
        provenance=provenance(target) if with_provenance else None,
        required=required,
    )


def matches(receipt: OperationEffectReceipt, *, op: ResourceOperation, f: OperationFence) -> bool:
    """Mirror of the executor's ``_receipt_matches`` (digest + exact fence echo)."""
    return isinstance(receipt, OperationEffectReceipt) and receipt.matches(
        operation_digest=resource_operation_digest(op), fence=f
    )


# --------------------------------------------------------------------------- #
# 1. Applying prompt/file operations
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_prompt_operation_writes_utf8_text_through_the_pod_workspace_port(
    port: PodWorkspaceOperationEffectPort, workspace: doubles.RecordingWorkspace
) -> None:
    op = operation(target=PROMPT_TARGET, payload={"body_md": "Инструкции агента"})
    receipt = await port.apply_operation(project_id=PROJECT_ID, operation=op, fence=fence())

    assert receipt.succeeded
    assert receipt.reason_code == REASON_APPLIED
    assert receipt.effect_unknown is False
    assert matches(receipt, op=op, f=fence())
    assert workspace.writes == [(RUNTIME_REF, PROMPT_TARGET, "Инструкции агента".encode())]


@pytest.mark.asyncio
async def test_prompt_body_keys_are_accepted_in_priority_order(
    port: PodWorkspaceOperationEffectPort, workspace: doubles.RecordingWorkspace
) -> None:
    body = operation(kind=OperationKind.PROMPT, payload={"body_md": "md", "content": "content"})
    await port.apply_operation(project_id=PROJECT_ID, operation=body, fence=fence())
    assert workspace.files[PROMPT_TARGET] == b"md"

    content = operation(kind=OperationKind.PROMPT, payload={"content": "content"})
    await port.apply_operation(project_id=PROJECT_ID, operation=content, fence=fence())
    assert workspace.files[PROMPT_TARGET] == b"content"


@pytest.mark.asyncio
async def test_file_operation_writes_inline_text(
    port: PodWorkspaceOperationEffectPort, workspace: doubles.RecordingWorkspace
) -> None:
    op = operation(kind=OperationKind.FILE, target=FILE_TARGET, payload={"content": "a,b\n1,2\n"})
    receipt = await port.apply_operation(project_id=PROJECT_ID, operation=op, fence=fence())

    assert receipt.succeeded and receipt.reason_code == REASON_APPLIED
    assert workspace.files[FILE_TARGET] == b"a,b\n1,2\n"


@pytest.mark.asyncio
async def test_file_operation_writes_asset_bytes_from_the_object_store(
    port: PodWorkspaceOperationEffectPort,
    workspace: doubles.RecordingWorkspace,
    assets: doubles.StaticAssets,
) -> None:
    assets.blobs["blob/immutable-a"] = b"\x00binary\xff"
    op = operation(
        kind=OperationKind.FILE,
        target=FILE_TARGET,
        payload={
            "file_ref": {
                "asset_id": "asset-a",
                "version_id": "asset-v3",
                "storage_key": "blob/immutable-a",
                "filename": "report.bin",
                "sha256": "sha256:" + "0" * 64,
            }
        },
    )
    receipt = await port.apply_operation(project_id=PROJECT_ID, operation=op, fence=fence())

    assert receipt.succeeded
    assert assets.keys == ["blob/immutable-a"]
    assert workspace.files[FILE_TARGET] == b"\x00binary\xff"


@pytest.mark.asyncio
async def test_file_operation_writes_inline_base64_body(
    port: PodWorkspaceOperationEffectPort, workspace: doubles.RecordingWorkspace
) -> None:
    raw = b"\x00\x01inline bytes"
    op = operation(
        kind=OperationKind.FILE,
        target=FILE_TARGET,
        payload={"data_base64": base64.b64encode(raw).decode("ascii")},
    )
    receipt = await port.apply_operation(project_id=PROJECT_ID, operation=op, fence=fence())

    assert receipt.succeeded
    assert workspace.files[FILE_TARGET] == raw


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        ({}, REASON_PROMPT_PAYLOAD_MISSING),
        ({"prompt_fragments": ["a", "b"]}, REASON_PROMPT_PAYLOAD_UNSUPPORTED),
        ({"files": {"a.md": "x"}}, REASON_PROMPT_PAYLOAD_UNSUPPORTED),
    ],
)
async def test_prompt_payload_without_inline_content_is_refused_and_writes_nothing(
    port: PodWorkspaceOperationEffectPort,
    workspace: doubles.RecordingWorkspace,
    payload: dict,
    expected: str,
) -> None:
    op = operation(kind=OperationKind.PROMPT, payload=payload)
    receipt = await port.apply_operation(project_id=PROJECT_ID, operation=op, fence=fence())

    assert receipt.succeeded is False
    assert receipt.reason_code == expected
    assert receipt.effect_unknown is False
    assert workspace.writes == []


@pytest.mark.asyncio
async def test_file_payload_without_text_asset_or_base64_is_refused(
    port: PodWorkspaceOperationEffectPort, workspace: doubles.RecordingWorkspace
) -> None:
    op = operation(kind=OperationKind.FILE, target=FILE_TARGET, payload={"asset_digest": "x"})
    receipt = await port.apply_operation(project_id=PROJECT_ID, operation=op, fence=fence())

    assert receipt.succeeded is False
    assert receipt.reason_code == REASON_FILE_PAYLOAD_UNSUPPORTED
    assert workspace.writes == []


@pytest.mark.asyncio
async def test_asset_that_the_payload_names_but_cannot_locate_is_refused(
    port: PodWorkspaceOperationEffectPort, workspace: doubles.RecordingWorkspace
) -> None:
    op = operation(
        kind=OperationKind.FILE,
        target=FILE_TARGET,
        payload={"asset_ref": {"asset_id": "asset-a", "version_id": "asset-v3"}},
    )
    receipt = await port.apply_operation(project_id=PROJECT_ID, operation=op, fence=fence())

    assert receipt.succeeded is False
    assert receipt.reason_code == REASON_ASSET_UNREADABLE
    assert workspace.writes == []


@pytest.mark.asyncio
async def test_unreadable_object_store_is_refused_not_swallowed(
    port: PodWorkspaceOperationEffectPort,
    workspace: doubles.RecordingWorkspace,
    assets: doubles.StaticAssets,
) -> None:
    assets.fail = True
    op = operation(
        kind=OperationKind.FILE,
        target=FILE_TARGET,
        payload={"storage_key": "blob/immutable-a"},
    )
    receipt = await port.apply_operation(project_id=PROJECT_ID, operation=op, fence=fence())

    assert receipt.succeeded is False
    assert receipt.reason_code == REASON_ASSET_UNREADABLE
    assert receipt.effect_unknown is False
    assert workspace.writes == []


@pytest.mark.asyncio
async def test_oversized_payload_is_refused_before_the_runtime_is_touched(
    port: PodWorkspaceOperationEffectPort,
    workspace: doubles.RecordingWorkspace,
    runtime: doubles.StaticRuntimeResolver,
) -> None:
    op = operation(payload={"body_md": "x" * (10 * 1024 * 1024 + 1)})
    receipt = await port.apply_operation(project_id=PROJECT_ID, operation=op, fence=fence())

    assert receipt.succeeded is False
    assert receipt.reason_code == REASON_PAYLOAD_TOO_LARGE
    assert workspace.writes == []
    assert runtime.calls == []


# --------------------------------------------------------------------------- #
# 2. Path safety — the critical negative surface
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "target",
    [
        "../escape.md",
        "../../etc/passwd",
        "prompts/../../escape.md",
        "a/../../b",
        "prompts/./apply-port.md",
        "prompts//apply-port.md",
        "prompts/",
        "%2e%2e%2fescape.md",
        "%252e%252e%252fescape.md",
        "prompts\\..\\..\\escape.md",
    ],
)
async def test_traversal_targets_are_refused_and_nothing_is_written(
    port: PodWorkspaceOperationEffectPort,
    workspace: doubles.RecordingWorkspace,
    runtime: doubles.StaticRuntimeResolver,
    target: str,
) -> None:
    op = operation(target=target)
    receipt = await port.apply_operation(project_id=PROJECT_ID, operation=op, fence=fence())

    assert receipt.succeeded is False
    assert receipt.reason_code == REASON_TARGET_UNSAFE
    assert receipt.effect_unknown is False
    # Nothing reached the Pod: no write, and no runtime lookup either.
    assert workspace.writes == []
    assert runtime.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("target", ["/etc/passwd", "/workspace/escape.md", "\\escape.md", "C:/escape.md"])
async def test_absolute_targets_are_refused(
    port: PodWorkspaceOperationEffectPort, workspace: doubles.RecordingWorkspace, target: str
) -> None:
    receipt = await port.apply_operation(
        project_id=PROJECT_ID, operation=operation(target=target), fence=fence()
    )

    assert receipt.succeeded is False
    assert receipt.reason_code == REASON_TARGET_ABSOLUTE
    assert workspace.writes == []


@pytest.mark.asyncio
@pytest.mark.parametrize("target", ["prompts/\x00apply-port.md", "prompts/apply\nport.md"])
async def test_nul_control_and_blank_targets_are_refused(
    port: PodWorkspaceOperationEffectPort, workspace: doubles.RecordingWorkspace, target: str
) -> None:
    receipt = await port.apply_operation(
        project_id=PROJECT_ID, operation=operation(target=target), fence=fence()
    )

    assert receipt.succeeded is False
    assert receipt.reason_code == REASON_TARGET_UNSAFE
    assert workspace.writes == []


def test_blank_target_is_refused_at_the_path_boundary() -> None:
    # ``ResourceOperation`` itself rejects an empty target, so the adapter's own
    # guard is asserted on the pure path function.
    for blank in ("", "   ", "\t"):
        with pytest.raises(ResourceEffectRefused) as refused:
            workspace_relative_path(blank)
        assert refused.value.reason_code == REASON_TARGET_MISSING


def test_workspace_relative_path_accepts_only_canonical_relative_paths() -> None:
    assert workspace_relative_path("prompts/apply-port.md") == "prompts/apply-port.md"
    assert workspace_relative_path("a/b/c.bin") == "a/b/c.bin"
    for bad in (".", "./a", "a/./b", "../a", "a/../b", "/a", "a//b", "a/", "C:/a"):
        with pytest.raises(ResourceEffectRefused):
            workspace_relative_path(bad)


def test_backslash_segments_are_normalized_then_still_confined() -> None:
    # ``a\b`` becomes ``a/b``: still inside the workspace, so it is allowed but
    # canonicalized rather than silently written under a Windows-looking name.
    assert workspace_relative_path("a\\b") == "a/b"


def test_over_long_target_is_refused() -> None:
    with pytest.raises(ResourceEffectRefused) as refused:
        workspace_relative_path("a" * 501)
    assert refused.value.reason_code == REASON_TARGET_TOO_LONG


# --------------------------------------------------------------------------- #
# 3. Fencing
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_receipt_echoes_the_exact_fence_and_operation_digest(
    port: PodWorkspaceOperationEffectPort,
) -> None:
    f = fence(generation=11, fence_token="fence-token-11")
    op = operation()
    receipt = await port.apply_operation(project_id=PROJECT_ID, operation=op, fence=f)

    assert receipt.fence_token == "fence-token-11"
    assert receipt.fence_generation == 11
    assert receipt.operation_digest == resource_operation_digest(op)
    assert matches(receipt, op=op, f=f)


@pytest.mark.asyncio
async def test_a_receipt_for_another_fence_does_not_match(
    port: PodWorkspaceOperationEffectPort,
) -> None:
    op = operation()
    receipt = await port.apply_operation(
        project_id=PROJECT_ID, operation=op, fence=fence(generation=11, fence_token="fence-11")
    )

    assert not matches(receipt, op=op, f=fence(generation=11, fence_token="fence-12"))
    assert not matches(receipt, op=op, f=fence(generation=12, fence_token="fence-11"))


@pytest.mark.asyncio
async def test_fence_minted_for_another_resource_is_refused_and_nothing_is_written(
    port: PodWorkspaceOperationEffectPort, workspace: doubles.RecordingWorkspace
) -> None:
    op = operation()
    foreign = fence(resource_key=resource_key_for_project(OTHER_PROJECT_ID))
    receipt = await port.apply_operation(project_id=PROJECT_ID, operation=op, fence=foreign)

    assert receipt.succeeded is False
    assert receipt.reason_code == REASON_FENCE_RESOURCE_MISMATCH
    assert workspace.writes == []


@pytest.mark.asyncio
async def test_a_fence_with_a_blank_project_is_refused(
    port: PodWorkspaceOperationEffectPort, workspace: doubles.RecordingWorkspace
) -> None:
    op = operation()
    receipt = await port.apply_operation(
        project_id="   ", operation=op, fence=fence(resource_key=resource_key_for_project(PROJECT_ID))
    )

    assert receipt.succeeded is False
    assert receipt.reason_code == REASON_PROJECT_SCOPE_INVALID
    assert workspace.writes == []


# --------------------------------------------------------------------------- #
# 4. Idempotency
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_repeat_under_the_same_fence_is_a_no_op_with_the_same_receipt(
    port: PodWorkspaceOperationEffectPort, workspace: doubles.RecordingWorkspace
) -> None:
    op = operation()
    f = fence()
    first = await port.apply_operation(project_id=PROJECT_ID, operation=op, fence=f)
    second = await port.apply_operation(project_id=PROJECT_ID, operation=op, fence=f)

    assert first == second
    assert len(workspace.writes) == 1


@pytest.mark.asyncio
async def test_a_new_fence_generation_writes_again(
    port: PodWorkspaceOperationEffectPort, workspace: doubles.RecordingWorkspace
) -> None:
    op = operation()
    await port.apply_operation(project_id=PROJECT_ID, operation=op, fence=fence(generation=7))
    await port.apply_operation(
        project_id=PROJECT_ID, operation=op, fence=fence(generation=8, fence_token="fence-token-8")
    )

    # Replay memory is fence-scoped: generation 8 is a distinct authority, so the
    # same bytes are written again rather than answered from generation 7's receipt.
    assert len(workspace.writes) == 2


@pytest.mark.asyncio
async def test_a_different_operation_digest_under_the_same_fence_writes_again(
    port: PodWorkspaceOperationEffectPort, workspace: doubles.RecordingWorkspace
) -> None:
    await port.apply_operation(
        project_id=PROJECT_ID, operation=operation(payload={"body_md": "one"}), fence=fence()
    )
    await port.apply_operation(
        project_id=PROJECT_ID, operation=operation(payload={"body_md": "two"}), fence=fence()
    )

    assert len(workspace.writes) == 2
    assert workspace.files[PROMPT_TARGET] == b"two"


@pytest.mark.asyncio
async def test_replay_memory_is_bounded_and_eviction_only_forgets(
    workspace: doubles.RecordingWorkspace, runtime: doubles.StaticRuntimeResolver
) -> None:
    port = PodWorkspaceOperationEffectPort(
        workspace=workspace, runtime=runtime, assets=doubles.StaticAssets(), applied_limit=1
    )
    first = operation(payload={"body_md": "one"})
    second = operation(payload={"body_md": "two"})

    await port.apply_operation(project_id=PROJECT_ID, operation=first, fence=fence())
    await port.apply_operation(project_id=PROJECT_ID, operation=second, fence=fence())
    replayed = await port.apply_operation(project_id=PROJECT_ID, operation=first, fence=fence())

    # The evicted receipt is no longer remembered, so the same bytes are rewritten
    # (idempotent by content) instead of a stale success being reported twice.
    assert replayed.succeeded is True
    assert len(workspace.writes) == 3
    assert workspace.files[PROMPT_TARGET] == b"one"


def test_applied_limit_must_be_positive() -> None:
    with pytest.raises(ValueError):
        PodWorkspaceOperationEffectPort(applied_limit=0)


# --------------------------------------------------------------------------- #
# 5. Compensation
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_compensation_deletes_the_written_file(
    port: PodWorkspaceOperationEffectPort, workspace: doubles.RecordingWorkspace
) -> None:
    op = operation()
    f = fence()
    await port.apply_operation(project_id=PROJECT_ID, operation=op, fence=f)
    assert PROMPT_TARGET in workspace.files

    receipt = await port.compensate_operation(project_id=PROJECT_ID, operation=op, fence=f)

    assert receipt.succeeded is True
    assert receipt.reason_code == REASON_COMPENSATED
    assert receipt.effect_unknown is False
    assert matches(receipt, op=op, f=f)
    assert workspace.deletes == [(RUNTIME_REF, PROMPT_TARGET)]
    assert PROMPT_TARGET not in workspace.files


@pytest.mark.asyncio
async def test_compensation_is_idempotent(
    port: PodWorkspaceOperationEffectPort, workspace: doubles.RecordingWorkspace
) -> None:
    op = operation()
    f = fence()
    await port.apply_operation(project_id=PROJECT_ID, operation=op, fence=f)
    first = await port.compensate_operation(project_id=PROJECT_ID, operation=op, fence=f)
    again = await port.compensate_operation(project_id=PROJECT_ID, operation=op, fence=f)

    assert first.reason_code == REASON_COMPENSATED
    assert again.succeeded is True
    assert again.reason_code == REASON_COMPENSATED
    assert matches(again, op=op, f=f)
    assert len(workspace.deletes) == 1


@pytest.mark.asyncio
async def test_a_later_write_after_compensation_can_be_compensated_again(
    port: PodWorkspaceOperationEffectPort, workspace: doubles.RecordingWorkspace
) -> None:
    op = operation()
    f = fence()
    await port.apply_operation(project_id=PROJECT_ID, operation=op, fence=f)
    await port.compensate_operation(project_id=PROJECT_ID, operation=op, fence=f)
    # A retry within the same generation rewrites the bytes ...
    await port.apply_operation(project_id=PROJECT_ID, operation=op, fence=f)
    assert PROMPT_TARGET in workspace.files
    # ... and a second compensation must delete them again, not answer "already done".
    receipt = await port.compensate_operation(project_id=PROJECT_ID, operation=op, fence=f)

    assert receipt.reason_code == REASON_COMPENSATED
    assert PROMPT_TARGET not in workspace.files
    assert len(workspace.deletes) == 2


@pytest.mark.asyncio
async def test_compensation_restores_absent_when_the_file_is_already_gone(
    port: PodWorkspaceOperationEffectPort, workspace: doubles.RecordingWorkspace
) -> None:
    op = operation()
    f = fence()
    await port.apply_operation(project_id=PROJECT_ID, operation=op, fence=f)
    workspace.files.pop(PROMPT_TARGET)  # the file vanished out of band

    receipt = await port.compensate_operation(project_id=PROJECT_ID, operation=op, fence=f)

    assert receipt.succeeded is True
    assert receipt.reason_code == REASON_COMPENSATED_ABSENT
    assert workspace.deletes == []


@pytest.mark.asyncio
async def test_compensation_without_a_recorded_write_refuses_and_deletes_nothing(
    port: PodWorkspaceOperationEffectPort, workspace: doubles.RecordingWorkspace
) -> None:
    workspace.put_file(PROMPT_TARGET, b"someone else's bytes")
    op = operation()

    receipt = await port.compensate_operation(project_id=PROJECT_ID, operation=op, fence=fence())

    assert receipt.succeeded is False
    assert receipt.reason_code == REASON_COMPENSATION_UNRECORDED
    # It will not delete a path it cannot prove it wrote.
    assert workspace.deletes == []
    assert PROMPT_TARGET in workspace.files


@pytest.mark.asyncio
async def test_compensation_of_a_fence_minted_for_another_resource_is_refused(
    port: PodWorkspaceOperationEffectPort, workspace: doubles.RecordingWorkspace
) -> None:
    op = operation()
    await port.apply_operation(project_id=PROJECT_ID, operation=op, fence=fence())
    receipt = await port.compensate_operation(
        project_id=PROJECT_ID,
        operation=op,
        fence=fence(resource_key=resource_key_for_project(OTHER_PROJECT_ID)),
    )

    assert receipt.succeeded is False
    assert receipt.reason_code == REASON_FENCE_RESOURCE_MISMATCH
    assert workspace.deletes == []


@pytest.mark.asyncio
async def test_compensation_refuses_a_directory_target(
    port: PodWorkspaceOperationEffectPort, workspace: doubles.RecordingWorkspace
) -> None:
    op = operation()
    f = fence()
    await port.apply_operation(project_id=PROJECT_ID, operation=op, fence=f)
    # The path was replaced by a directory after the write.
    workspace.files.pop(PROMPT_TARGET)
    workspace.put_directory(PROMPT_TARGET)

    receipt = await port.compensate_operation(project_id=PROJECT_ID, operation=op, fence=f)

    assert receipt.succeeded is False
    assert receipt.reason_code == REASON_COMPENSATION_CONFLICT
    assert workspace.deletes == []


@pytest.mark.asyncio
async def test_compensation_for_an_unsupported_kind_is_refused(
    port: PodWorkspaceOperationEffectPort, workspace: doubles.RecordingWorkspace
) -> None:
    receipt = await port.compensate_operation(
        project_id=PROJECT_ID,
        operation=operation(kind=OperationKind.ENV, target=".env", payload={"A": "1"}),
        fence=fence(),
    )

    assert receipt.succeeded is False
    assert receipt.reason_code == unsupported_reason_code(OperationKind.ENV)
    assert workspace.deletes == []


@pytest.mark.asyncio
async def test_a_failed_delete_is_an_unknown_compensation_not_a_success(
    port: PodWorkspaceOperationEffectPort, workspace: doubles.RecordingWorkspace
) -> None:
    op = operation()
    f = fence()
    await port.apply_operation(project_id=PROJECT_ID, operation=op, fence=f)
    workspace.fail_deletes = True

    receipt = await port.compensate_operation(project_id=PROJECT_ID, operation=op, fence=f)

    assert receipt.succeeded is False
    assert receipt.effect_unknown is True


# --------------------------------------------------------------------------- #
# 6. Unsupported kinds
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("kind", "target", "payload", "expected"),
    [
        (OperationKind.ENV, ".env", {"MODEL": "x"}, "resource_effect_env_unsupported"),
        (OperationKind.MCP, "mcp/servers.json", {"manifest": {}}, "resource_effect_mcp_unsupported"),
        (OperationKind.SCOPE, "scope/project-1", {}, "resource_effect_scope_unsupported"),
    ],
)
async def test_env_mcp_and_scope_are_refused_with_named_codes(
    port: PodWorkspaceOperationEffectPort,
    workspace: doubles.RecordingWorkspace,
    runtime: doubles.StaticRuntimeResolver,
    kind: OperationKind,
    target: str,
    payload: dict,
    expected: str,
) -> None:
    op = operation(kind=kind, target=target, payload=payload)
    receipt = await port.apply_operation(project_id=PROJECT_ID, operation=op, fence=fence())

    assert receipt.succeeded is False
    assert receipt.reason_code == expected
    assert receipt.effect_unknown is False
    assert matches(receipt, op=op, f=fence())
    # A refusal must not look like an apply: nothing was written, nothing looked up.
    assert workspace.writes == []
    assert runtime.calls == []


def test_unsupported_reason_code_is_derived_from_the_kind() -> None:
    assert unsupported_reason_code("env") == "resource_effect_env_unsupported"
    assert unsupported_reason_code(OperationKind.MCP) == "resource_effect_mcp_unsupported"


# --------------------------------------------------------------------------- #
# 7. Runtime resolution — the Pod must be a running sandbox
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_missing_runtime_resolver_is_a_named_refusal(
    workspace: doubles.RecordingWorkspace,
) -> None:
    port = PodWorkspaceOperationEffectPort(workspace=workspace)
    receipt = await port.apply_operation(
        project_id=PROJECT_ID, operation=operation(), fence=fence()
    )

    assert receipt.succeeded is False
    assert receipt.reason_code == REASON_RUNTIME_RESOLVER_MISSING
    assert workspace.writes == []


@pytest.mark.asyncio
async def test_pod_not_running_refuses_without_writing(
    port: PodWorkspaceOperationEffectPort,
    workspace: doubles.RecordingWorkspace,
    runtime: doubles.StaticRuntimeResolver,
) -> None:
    runtime.ref = None
    receipt = await port.apply_operation(
        project_id=PROJECT_ID, operation=operation(), fence=fence()
    )

    assert receipt.succeeded is False
    assert receipt.reason_code == REASON_RUNTIME_NOT_RUNNING
    assert workspace.writes == []
    assert runtime.calls == [PROJECT_ID]


@pytest.mark.asyncio
async def test_unreadable_runtime_is_a_refusal_not_an_exception(
    port: PodWorkspaceOperationEffectPort,
    workspace: doubles.RecordingWorkspace,
    runtime: doubles.StaticRuntimeResolver,
) -> None:
    runtime.raise_error = True
    receipt = await port.apply_operation(
        project_id=PROJECT_ID, operation=operation(), fence=fence()
    )

    assert receipt.succeeded is False
    assert receipt.reason_code == REASON_RUNTIME_UNAVAILABLE
    assert workspace.writes == []


@pytest.mark.parametrize(
    ("runtime_view", "expected"),
    [
        (None, None),
        ({}, None),
        ({"stub": True, "observed_state": "running", "k8s_pod_name": "pod-1"}, None),
        ({"stub": False, "observed_state": "pending", "k8s_pod_name": "pod-1"}, None),
        ({"stub": False, "observed_state": "running", "k8s_pod_name": ""}, None),
        ({"stub": False, "observed_state": "running", "k8s_pod_name": "object-ws:prj"}, None),
        ({"stub": False, "observed_state": "running", "k8s_pod_name": "pod-1"}, "pod-1"),
        ({"stub": False, "observed_state": "running", "runtime_ref": "pod-2"}, "pod-2"),
    ],
)
def test_running_runtime_ref_applies_the_runtime_guard_predicates(
    runtime_view: dict | None, expected: str | None
) -> None:
    assert running_runtime_ref(runtime_view) == expected


# --------------------------------------------------------------------------- #
# 8. Transport failures are unknown, never silent successes
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_a_failed_write_is_an_unknown_effect(
    port: PodWorkspaceOperationEffectPort, workspace: doubles.RecordingWorkspace
) -> None:
    workspace.fail_writes = True
    receipt = await port.apply_operation(
        project_id=PROJECT_ID, operation=operation(), fence=fence()
    )

    assert receipt.succeeded is False
    assert receipt.reason_code == REASON_WRITE_UNKNOWN
    assert receipt.effect_unknown is True


@pytest.mark.asyncio
async def test_a_write_that_fails_after_the_bytes_landed_is_still_unknown(
    port: PodWorkspaceOperationEffectPort, workspace: doubles.RecordingWorkspace
) -> None:
    workspace.fail_after_write = True
    receipt = await port.apply_operation(
        project_id=PROJECT_ID, operation=operation(), fence=fence()
    )

    assert receipt.succeeded is False
    assert receipt.effect_unknown is True
    assert workspace.files[PROMPT_TARGET] == PAYLOAD_TEXT.encode()


# --------------------------------------------------------------------------- #
# 9. Rollback is a refusal, not a stub
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_rollback_is_a_typed_refusal_the_ledger_cannot_read_as_success(
    port: PodWorkspaceOperationEffectPort, workspace: doubles.RecordingWorkspace
) -> None:
    f = fence(generation=9, fence_token="fence-token-9")
    receipt = await port.apply_rollback(
        project_id=PROJECT_ID,
        target_generation=3,
        target_revision_id="rrev-3",
        fence=f,
    )

    assert receipt.succeeded is False
    assert receipt.reason_code == REASON_ROLLBACK_UNSUPPORTED
    assert receipt.effect_unknown is False
    # The fence is still echoed (a receipt is a receipt) ...
    assert receipt.fence_token == f.fence_token
    assert receipt.fence_generation == f.generation
    # ... but the digest is deliberately NOT the ledger's rollback expectation, so
    # ``_receipt_matches`` fails and the executor answers ``rollback_effect_unknown``
    # instead of recording a rollback that never ran (the executor ignores
    # ``succeeded`` on that branch).
    expected = canonical_digest({"rollback_of_generation": 3})
    assert receipt.operation_digest != expected
    assert not receipt.matches(operation_digest=expected, fence=f)
    assert workspace.writes == []


def test_rollback_support_flag_is_false() -> None:
    assert PodWorkspaceOperationEffectPort.rollback_supported is False


def test_composition_exposes_the_port_but_not_a_rollback_capability() -> None:
    class _UnusedSessionFactory:
        def __call__(self):  # pragma: no cover - never invoked in this check
            raise AssertionError("session factory must not be used at composition time")

    built = build_pod_workspace_effect_port(session_factory=_UnusedSessionFactory())  # type: ignore[arg-type]

    assert isinstance(built, PodWorkspaceOperationEffectPort)
    assert built.rollback_supported is False


# --------------------------------------------------------------------------- #
# 10. Reasons codes are valid ledger codes (no secrets, right shape)
# --------------------------------------------------------------------------- #


def test_every_reason_code_is_a_redacted_lower_case_code() -> None:
    reasons = {
        REASON_APPLIED,
        REASON_ASSET_UNREADABLE,
        REASON_COMPENSATED,
        REASON_COMPENSATED_ABSENT,
        REASON_COMPENSATION_CONFLICT,
        REASON_COMPENSATION_UNKNOWN,
        REASON_COMPENSATION_UNRECORDED,
        REASON_FENCE_RESOURCE_MISMATCH,
        REASON_FILE_PAYLOAD_UNSUPPORTED,
        REASON_PAYLOAD_TOO_LARGE,
        REASON_PROMPT_PAYLOAD_MISSING,
        REASON_PROMPT_PAYLOAD_UNSUPPORTED,
        REASON_PROJECT_SCOPE_INVALID,
        REASON_ROLLBACK_UNSUPPORTED,
        REASON_RUNTIME_NOT_RUNNING,
        REASON_RUNTIME_RESOLVER_MISSING,
        REASON_RUNTIME_UNAVAILABLE,
        REASON_TARGET_ABSOLUTE,
        REASON_TARGET_MISSING,
        REASON_TARGET_TOO_LONG,
        REASON_TARGET_UNSAFE,
        REASON_WRITE_UNKNOWN,
        *(unsupported_reason_code(kind) for kind in OperationKind),
    }
    for reason in reasons:
        assert _CODE.fullmatch(reason), reason
        # ``RedactedDiagnostic`` is the ledger's own validator: it enforces the code
        # shape *and* rejects names containing credential material.
        assert RedactedDiagnostic("apply", reason).reason_code == reason
        assert "secret" not in reason
        assert "token" not in reason
        assert "password" not in reason


# --------------------------------------------------------------------------- #
# 11. Nothing sensitive reaches the log
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_no_payload_path_or_secret_reaches_the_log(
    port: PodWorkspaceOperationEffectPort,
    workspace: doubles.RecordingWorkspace,
    runtime: doubles.StaticRuntimeResolver,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    op = operation(payload={"body_md": f"line with {SECRET_TEXT} inside"})
    f = fence()

    with caplog.at_level(logging.DEBUG, logger="prodavan"):
        await port.apply_operation(project_id=PROJECT_ID, operation=op, fence=f)
        await port.apply_operation(project_id=PROJECT_ID, operation=op, fence=f)
        await port.compensate_operation(project_id=PROJECT_ID, operation=op, fence=f)
        await port.compensate_operation(project_id=PROJECT_ID, operation=op, fence=f)
        workspace.fail_writes = True
        await port.apply_operation(project_id=PROJECT_ID, operation=operation(), fence=f)
        runtime.raise_error = True
        await port.apply_operation(
            project_id=PROJECT_ID, operation=operation(target="files/other.csv"), fence=f
        )

    text = caplog.text
    assert text  # the adapter does log its decisions
    assert SECRET_TEXT not in text
    assert PAYLOAD_TEXT not in text
    assert PROMPT_TARGET not in text
    assert "files/other.csv" not in text
    assert "do-not-leak" not in text  # the transport double's own message
    for marker in ("password", "secret", "api_key", "private_key"):
        assert marker not in text.lower()


@pytest.mark.asyncio
async def test_refusals_log_only_the_kind_and_reason_code(
    port: PodWorkspaceOperationEffectPort, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.INFO, logger="prodavan"):
        await port.apply_operation(
            project_id=PROJECT_ID,
            operation=operation(kind=OperationKind.MCP, target="mcp/x.json", payload={}),
            fence=fence(),
        )

    assert "resource_effect_mcp_unsupported" in caplog.text
    assert "mcp/x.json" not in caplog.text


# --------------------------------------------------------------------------- #
# 12. Refusal objects do not leak through their string form
# --------------------------------------------------------------------------- #


def test_refused_exception_stringifies_to_a_reason_code_only() -> None:
    refusal = ResourceEffectRefused(REASON_TARGET_UNSAFE)
    assert str(refusal) == REASON_TARGET_UNSAFE
    assert refusal.effect_unknown is False
    assert ResourceEffectRefused(REASON_WRITE_UNKNOWN, effect_unknown=True).effect_unknown is True
