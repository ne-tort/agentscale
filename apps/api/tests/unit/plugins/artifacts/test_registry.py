"""Unit-тесты реестра артефактов плагинов (F4).

Ключевые сценарии: publish только после verify, QUARANTINED без публикации,
ARTIFACT_DIGEST_CONFLICT, rollback на неподписанный digest → ROLLBACK_INVALID.
"""

from __future__ import annotations

from dataclasses import replace

from prodavan.application.dynamic_app_registry.contracts import (
    QuarantineEvidence,
    RegistryOutcome,
    RegistryReason,
    RollbackCommand,
    RollbackConflictError,
)
import pytest

pytestmark = pytest.mark.asyncio

from prodavan.application.plugins.artifacts import (
    COMPATIBILITY_ERROR_CODE,
    DIGEST_CONFLICT_ERROR_CODE,
    ArtifactError,
    ArtifactErrorCode,
    PluginArtifactRegistry,
)
from prodavan.application.plugins.artifacts.registry import PublicationMetadata

from .conftest import (
    DIGEST_A,
    DIGEST_B,
    DIGEST_UNSIGNED,
    full_grant,
    ref,
    valid_manifest,
)


def _verified(verifier, manifest=None, digest: str = DIGEST_A, **kwargs):
    """Успешная проверка → evidence для publish (вариант A: оба внешних флага)."""
    flags = {"grant": full_grant(), "scan_verified": True, "csp_verified": True}
    flags.update(kwargs)
    return verifier.verify(
        ref(digest),
        manifest or valid_manifest(),
        **flags,
    )


def publication_metadata() -> PublicationMetadata:
    return PublicationMetadata(
        source_id="agentscale/plugins",
        commit_sha="a" * 40,
        backend_digest="sha256:" + "b" * 64,
        backend_image_ref="registry.example/backend@sha256:" + "b" * 64,
        run_url="https://github.com/agentscale/plugins/actions/runs/42",
    )


async def _publish(registry, ref, manifest, evidence, **kwargs):
    kwargs.setdefault("metadata", publication_metadata())
    return await registry.publish_async(ref, manifest, evidence, **kwargs)



def _assert_no_publication(registry: PluginArtifactRegistry, plugin_id: str = "mod_equipment") -> None:
    assert registry.current(plugin_id) is None
    assert registry.get(plugin_id, "1.2.0") is None


async def test_publish_after_successful_verify(registry: PluginArtifactRegistry, verifier, store) -> None:
    manifest = valid_manifest()
    outcome = _verified(verifier, manifest)
    result = await _publish(registry, ref(), manifest, outcome)
    assert result.outcome is RegistryOutcome.APPLIED
    record = registry.current("mod_equipment")
    assert record is not None
    assert record.digest == DIGEST_A
    assert record.image_ref == ref().image_ref()
    assert record.manifest == manifest
    assert record.publication is not None
    assert record.publication.commit_sha == "a" * 40
    assert record.verification is not None
    assert record.verification.identity
    assert record.verification.issuer
    # Проекция в доменный AppVersion (переиспользуем тип, не копируем).
    assert result.version is not None
    assert result.version.artifact_digest == DIGEST_A
    assert result.version.manifest["plugin_manifest"] == manifest
    assert result.version.manifest["protocol"] == "1.0"  # legacy-обёртка
    assert result.version.status.value == "published"
    assert store.lifecycle_log[0][1].startswith("publish ")


async def test_publish_without_evidence_is_quarantined(registry: PluginArtifactRegistry, store) -> None:
    """Публикация без успешного verify → QUARANTINED, publish не происходит."""
    result = await _publish(registry, ref(), valid_manifest(), object())
    assert result.outcome is RegistryOutcome.QUARANTINED
    assert isinstance(result.quarantine, QuarantineEvidence)
    assert result.quarantine.metadata["artifact_error_code"] == "QUARANTINED"
    _assert_no_publication(registry)
    assert len(store.quarantine_log) == 1


async def test_quarantined_evidence_does_not_publish(registry: PluginArtifactRegistry, verifier, cosign) -> None:
    """cosign verify упал → QUARANTINED, publish не происходит."""
    manifest = valid_manifest()
    cosign.verified = False
    outcome = verifier.verify(
        ref(), manifest, grant=full_grant(), scan_verified=True, csp_verified=True
    )
    assert outcome.outcome is RegistryOutcome.QUARANTINED
    result = await _publish(registry, ref(), manifest, outcome)
    assert result.outcome is RegistryOutcome.QUARANTINED
    _assert_no_publication(registry)


async def test_forged_evidence_does_not_publish(registry: PluginArtifactRegistry) -> None:
    """Сфабрикованное свидетельство отклоняется (печать verifier'а)."""
    result = await _publish(registry, ref(), valid_manifest(), {"verified": True})
    assert result.outcome is RegistryOutcome.QUARANTINED
    _assert_no_publication(registry)


async def test_repeat_digest_with_other_manifest_is_artifact_digest_conflict(
    registry: PluginArtifactRegistry, verifier, store
) -> None:
    """Повторный digest с другим manifest → ARTIFACT_DIGEST_CONFLICT."""
    manifest_a = valid_manifest()
    first = _verified(verifier, manifest_a)
    assert (await _publish(registry, ref(), manifest_a, first)).outcome is RegistryOutcome.APPLIED

    manifest_b = valid_manifest(version="1.3.0", capabilities=["cache"])
    second = _verified(verifier, manifest_b)
    result = await _publish(registry, ref(), manifest_b, second)
    assert result.outcome is RegistryOutcome.QUARANTINED
    assert result.reason is RegistryReason.ARTIFACT_DIGEST_CONFLICT
    assert result.quarantine.metadata["artifact_error_code"] == "DIGEST_CONFLICT"
    assert DIGEST_CONFLICT_ERROR_CODE == "ARTIFACT_DIGEST_CONFLICT"
    # Текущая версия не изменилась.
    assert registry.current("mod_equipment").version == "1.2.0"


async def test_idempotent_republish_same_digest(
    registry: PluginArtifactRegistry, verifier, store
) -> None:
    manifest = valid_manifest()
    outcome = _verified(verifier, manifest)
    first = await _publish(registry, ref(), manifest, outcome)
    assert first.outcome is RegistryOutcome.APPLIED
    again = await _publish(registry, ref(), manifest, outcome)
    assert again.outcome is RegistryOutcome.APPLIED
    assert again.version == first.version
    assert len(store.versions("mod_equipment")) == 1
    assert len(store.lifecycle_log) == 1


async def test_legacy_sync_promote_is_fail_closed_without_process_lifecycle(
    registry: PluginArtifactRegistry, verifier, store
) -> None:
    manifest = valid_manifest()
    outcome = _verified(verifier, manifest)
    await _publish(registry, ref(), manifest, outcome)
    before = store.lifecycle_log
    current = registry.current("mod_equipment")
    assert current is not None

    with pytest.raises(ArtifactError) as caught:
        registry._promote(current, "admin")

    assert caught.value.code is ArtifactErrorCode.QUARANTINED
    assert store.lifecycle_log == before
    assert registry.current("mod_equipment").digest == current.digest


async def test_incompatible_manifest_is_not_published(registry: PluginArtifactRegistry, verifier) -> None:
    """Неизвестная capability → INCOMPATIBLE, запись не публикуется."""
    manifest = valid_manifest(capabilities=["cache", "alien_capability"])
    outcome = verifier.verify(
        ref(), manifest, grant=full_grant(), scan_verified=True, csp_verified=True
    )
    assert outcome.outcome is RegistryOutcome.QUARANTINED
    result = await _publish(registry, ref(), manifest, outcome)
    assert result.outcome is RegistryOutcome.QUARANTINED
    _assert_no_publication(registry)
    assert COMPATIBILITY_ERROR_CODE == "INCOMPATIBLE"


async def test_publish_snapshot_of_both_flags_works(registry: PluginArtifactRegistry, verifier) -> None:
    """Базовый publish(ref, manifest, evidence) берёт ОБА флага из evidence."""
    manifest = valid_manifest()
    outcome = _verified(verifier, manifest)
    assert outcome.evidence.scan_verified is True
    assert outcome.evidence.csp_verified is True
    assert (await _publish(registry, ref(), manifest, outcome)).outcome is RegistryOutcome.APPLIED


async def test_publish_with_explicit_false_csp_is_quarantined(
    registry: PluginArtifactRegistry, verifier
) -> None:
    """Явный ``csp_verified=False`` в publish = принудительный отказ (fail-closed)."""
    manifest = valid_manifest()
    outcome = _verified(verifier, manifest)
    result = await _publish(registry, ref(), manifest, outcome, csp_verified=False)
    assert result.outcome is RegistryOutcome.QUARANTINED
    _assert_no_publication(registry)


async def test_publish_evidence_without_csp_flag_is_quarantined(
    registry: PluginArtifactRegistry, verifier
) -> None:
    """Evidence без ``csp_verified`` не публикуется, даже если gate вызван вручную."""
    manifest = valid_manifest()
    outcome = _verified(verifier, manifest, csp_verified=False)
    assert outcome.outcome is RegistryOutcome.QUARANTINED  # verify сам отказал
    result = await _publish(registry, ref(), manifest, outcome)
    assert result.outcome is RegistryOutcome.QUARANTINED
    _assert_no_publication(registry)


async def test_compatibility_error_code(registry: PluginArtifactRegistry) -> None:
    assert registry.compatibility_error_code() == "INCOMPATIBLE"
    assert COMPATIBILITY_ERROR_CODE == "INCOMPATIBLE"


async def test_get_and_current(registry: PluginArtifactRegistry, verifier) -> None:
    manifest = valid_manifest()
    outcome = _verified(verifier, manifest)
    await _publish(registry, ref(), manifest, outcome)
    assert registry.get("mod_equipment", "1.2.0") is not None
    assert await registry.get_async("mod_equipment", "1.2.0") is not None
    assert registry.get("mod_equipment", "9.9.9") is None
    assert await registry.get_async("mod_equipment", "9.9.9") is None
    assert registry.current("mod_equipment") is not None
    assert await registry.current_async("mod_equipment") is not None
    assert registry.current("unknown") is None
    assert await registry.current_async("unknown") is None


async def test_quarantine_marks_version_and_clears_current(
    registry: PluginArtifactRegistry, verifier, store
) -> None:
    manifest = valid_manifest()
    outcome = _verified(verifier, manifest)
    await _publish(registry, ref(), manifest, outcome)
    result = registry.quarantine(ref(), manifest, reason="compromised signature", actor="admin")
    assert result.outcome is RegistryOutcome.QUARANTINED
    record = store.by_digest(DIGEST_A)
    assert record is not None
    assert record.status.value == "revoked"
    assert record.quarantine is result.quarantine
    assert registry.current("mod_equipment") is None


async def test_rollback_to_unsigned_digest_is_invalid(registry: PluginArtifactRegistry, verifier, store) -> None:
    """Rollback на неподписанный digest → ROLLBACK_INVALID."""
    manifest_v1 = valid_manifest(version="1.1.0")
    manifest_v2 = valid_manifest(version="1.2.0")
    out_v1 = _verified(verifier, manifest_v1, digest=DIGEST_B)
    out_v2 = _verified(verifier, manifest_v2, digest=DIGEST_A)
    assert (await _publish(registry, ref(DIGEST_B), manifest_v1, out_v1)).outcome is RegistryOutcome.APPLIED
    assert (await _publish(registry, ref(DIGEST_A), manifest_v2, out_v2)).outcome is RegistryOutcome.APPLIED

    # Неподписанный digest отсутствует в реестре и не подтверждён cosign.
    result = registry.rollback(
        "mod_equipment",
        DIGEST_UNSIGNED,
        actor="admin",
        reason="incident",
        cosign_verified=False,
    )
    assert result.outcome is RegistryOutcome.QUARANTINED
    assert result.reason is RegistryReason.ROLLBACK_TARGET_INVALID
    assert result.quarantine.metadata["artifact_error_code"] == "ROLLBACK_INVALID"
    assert ArtifactErrorCode.ROLLBACK_INVALID.value == "ROLLBACK_INVALID"
    assert registry.current("mod_equipment").digest == DIGEST_A


async def test_rollback_to_published_signed_digest(registry: PluginArtifactRegistry, verifier, store) -> None:
    """Успешный откат = деплой предыдущего immutable digest через RollbackCommand."""
    manifest_v1 = valid_manifest(version="1.1.0")
    manifest_v2 = valid_manifest(version="1.2.0")
    out_v1 = _verified(verifier, manifest_v1, digest=DIGEST_B)
    out_v2 = _verified(verifier, manifest_v2, digest=DIGEST_A)
    await _publish(registry, ref(DIGEST_B), manifest_v1, out_v1)
    await _publish(registry, ref(DIGEST_A), manifest_v2, out_v2)

    result = registry.rollback(
        "mod_equipment",
        DIGEST_B,
        actor="admin",
        reason="regression in 1.2.0",
        cosign_verified=True,
        expected_current_version="1.2.0",
    )
    assert result.outcome is RegistryOutcome.APPLIED
    assert registry.current("mod_equipment").digest == DIGEST_B
    assert result.version is not None
    assert result.version.manifest_version == "1.1.0"
    # В лог lifecycle попала команда, построенная из доменного RollbackCommand.
    assert store.lifecycle_log[-1][1] == "rollback mod_equipment@1.1.0"


async def test_async_rollback_uses_stored_current_and_lifecycle(
    registry: PluginArtifactRegistry, verifier, store
) -> None:
    manifest_v1 = valid_manifest(version="1.1.0")
    manifest_v2 = valid_manifest(version="1.2.0")
    out_v1 = _verified(verifier, manifest_v1, digest=DIGEST_B)
    out_v2 = _verified(verifier, manifest_v2, digest=DIGEST_A)
    await _publish(registry, ref(DIGEST_B), manifest_v1, out_v1)
    await _publish(registry, ref(DIGEST_A), manifest_v2, out_v2)

    result = await registry.rollback_async(
        "mod_equipment",
        DIGEST_B,
        actor="admin",
        expected_current_version="1.2.0",
        cosign_verified=True,
    )

    assert result.outcome is RegistryOutcome.APPLIED
    current = await registry.current_async("mod_equipment")
    assert current is not None and current.digest == DIGEST_B
    assert result.version is not None and result.version.published_at == current.published_at
    assert store.lifecycle_log[-1][1] == "rollback mod_equipment@1.1.0"


async def test_current_target_sync_and_async_rollback_reach_exact_replay(
    registry: PluginArtifactRegistry, verifier, store
) -> None:
    manifest_v1 = valid_manifest(version="1.1.0")
    manifest_v2 = valid_manifest(version="1.2.0")
    await _publish(
        registry, ref(DIGEST_B), manifest_v1,
        _verified(verifier, manifest_v1, digest=DIGEST_B),
    )
    await _publish(
        registry, ref(DIGEST_A), manifest_v2,
        _verified(verifier, manifest_v2, digest=DIGEST_A),
    )
    initial = registry.rollback(
        "mod_equipment",
        DIGEST_B,
        actor="admin",
        expected_current_version="1.2.0",
        expected_current_lifecycle_version=1,
        cosign_verified=True,
    )
    assert initial.outcome is RegistryOutcome.APPLIED
    before = store.lifecycle_log

    sync_calls = []
    original_sync = store.rollback

    def spy_sync(**kwargs):
        sync_calls.append(kwargs)
        return original_sync(**kwargs)

    store.rollback = spy_sync  # type: ignore[method-assign]
    replayed_sync = registry.rollback(
        "mod_equipment",
        DIGEST_B,
        actor="admin",
        expected_current_version="1.2.0",
        cosign_verified=True,
    )
    assert replayed_sync.outcome is RegistryOutcome.APPLIED
    assert len(sync_calls) == 1
    assert store.lifecycle_log == before

    async_calls = []
    original_async = store.rollback_async

    async def spy_async(**kwargs):
        async_calls.append(kwargs)
        return await original_async(**kwargs)

    store.rollback_async = spy_async  # type: ignore[method-assign]
    replayed_async = await registry.rollback_async(
        "mod_equipment",
        DIGEST_B,
        actor="admin",
        expected_current_version="1.2.0",
        expected_current_lifecycle_version=1,
        cosign_verified=True,
    )
    assert replayed_async.outcome is RegistryOutcome.APPLIED
    assert len(async_calls) == 1
    assert store.lifecycle_log == before
    denied_without_source = registry.rollback(
        "mod_equipment", DIGEST_B, actor="admin", cosign_verified=True
    )
    assert denied_without_source.outcome is RegistryOutcome.QUARANTINED
    assert denied_without_source.reason is RegistryReason.ROLLBACK_TARGET_INVALID
    assert len(sync_calls) == 1
    assert store.lifecycle_log == before
    current = await registry.current_async("mod_equipment")
    assert current is not None
    assert replayed_async.version.published_at == current.published_at


async def test_inmemory_rollback_rejects_reason_and_actor_mismatch(
    registry: PluginArtifactRegistry, verifier, store
) -> None:
    manifest_v1 = valid_manifest(version="1.1.0")
    manifest_v2 = valid_manifest(version="1.2.0")
    await _publish(registry, ref(DIGEST_B), manifest_v1, _verified(verifier, manifest_v1, DIGEST_B))
    await _publish(registry, ref(DIGEST_A), manifest_v2, _verified(verifier, manifest_v2, DIGEST_A))
    target = store.by_digest(DIGEST_B)
    command = RollbackCommand(
        definition_id="mod_equipment",
        target_version_id="1.1.0",
        actor="admin",
        reason="first reason",
        expected_current_version_id="1.2.0",
        expected_artifact_digest=DIGEST_B,
        expected_current_lifecycle_version=1,
    )
    await store.rollback_async(
        target=target, command=command, actor="admin", detail="rollback first reason"
    )
    before = store.lifecycle_log

    with pytest.raises(RollbackConflictError):
        await store.rollback_async(
            target=target,
            command=replace(command, reason="different reason"),
            actor="admin",
            detail="rollback first reason",
        )
    with pytest.raises(RollbackConflictError):
        await store.rollback_async(
            target=target,
            command=replace(command, actor="other"),
            actor="admin",
            detail="rollback first reason",
        )

    assert store.lifecycle_log == before
    assert (await registry.current_async("mod_equipment")).digest == DIGEST_B


async def test_rollback_command_is_reused_not_copied(store, verifier, registry) -> None:
    """Реестр использует RollbackCommand из dynamic_app_registry (импорт)."""
    manifest_v1 = valid_manifest(version="1.1.0")
    manifest_v2 = valid_manifest(version="1.2.0")
    await _publish(registry, ref(DIGEST_B), manifest_v1, _verified(verifier, manifest_v1, DIGEST_B))
    await _publish(registry, ref(DIGEST_A), manifest_v2, _verified(verifier, manifest_v2, DIGEST_A))

    captured = []
    original = store.rollback

    def spy(*, target, command, actor, detail):
        captured.append(command)
        return original(target=target, command=command, actor=actor, detail=detail)

    store.rollback = spy  # type: ignore[method-assign]
    registry.rollback("mod_equipment", DIGEST_B, actor="admin", reason="r", cosign_verified=True)
    assert isinstance(captured[0], RollbackCommand)
    assert captured[0].target_version_id == "1.1.0"
    assert captured[0].expected_artifact_digest == DIGEST_B


async def test_sync_publish_is_absent_and_metadata_is_required(registry, verifier) -> None:
    assert not hasattr(registry, "publish")
    with pytest.raises(TypeError):
        await registry.publish_async(ref(), valid_manifest(), _verified(verifier))
