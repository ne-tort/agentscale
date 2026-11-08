"""Unit-тесты реестра артефактов плагинов (F4).

Ключевые сценарии: publish только после verify, QUARANTINED без публикации,
ARTIFACT_DIGEST_CONFLICT, rollback на неподписанный digest → ROLLBACK_INVALID.
"""

from __future__ import annotations

from prodavan.application.dynamic_app_registry.contracts import (
    QuarantineEvidence,
    RegistryOutcome,
    RegistryReason,
    RollbackCommand,
)
from prodavan.application.plugins.artifacts import (
    COMPATIBILITY_ERROR_CODE,
    DIGEST_CONFLICT_ERROR_CODE,
    ArtifactErrorCode,
    PluginArtifactRegistry,
)
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


def _assert_no_publication(registry: PluginArtifactRegistry, plugin_id: str = "mod_equipment") -> None:
    assert registry.current(plugin_id) is None
    assert registry.get(plugin_id, "1.2.0") is None


def test_publish_after_successful_verify(registry: PluginArtifactRegistry, verifier, store) -> None:
    manifest = valid_manifest()
    outcome = _verified(verifier, manifest)
    result = registry.publish(ref(), manifest, outcome)
    assert result.outcome is RegistryOutcome.APPLIED
    record = registry.current("mod_equipment")
    assert record is not None
    assert record.digest == DIGEST_A
    assert record.image_ref == ref().image_ref()
    assert record.manifest == manifest
    # Проекция в доменный AppVersion (переиспользуем тип, не копируем).
    assert result.version is not None
    assert result.version.artifact_digest == DIGEST_A
    assert result.version.manifest["plugin_manifest"] == manifest
    assert result.version.manifest["protocol"] == "1.0"  # legacy-обёртка
    assert result.version.status.value == "published"
    assert store.lifecycle_log[0][1].startswith("publish ")


def test_publish_without_evidence_is_quarantined(registry: PluginArtifactRegistry, store) -> None:
    """Публикация без успешного verify → QUARANTINED, publish не происходит."""
    result = registry.publish(ref(), valid_manifest(), object())
    assert result.outcome is RegistryOutcome.QUARANTINED
    assert isinstance(result.quarantine, QuarantineEvidence)
    assert result.quarantine.metadata["artifact_error_code"] == "QUARANTINED"
    _assert_no_publication(registry)
    assert len(store.quarantine_log) == 1


def test_quarantined_evidence_does_not_publish(registry: PluginArtifactRegistry, verifier, cosign) -> None:
    """cosign verify упал → QUARANTINED, publish не происходит."""
    manifest = valid_manifest()
    cosign.verified = False
    outcome = verifier.verify(
        ref(), manifest, grant=full_grant(), scan_verified=True, csp_verified=True
    )
    assert outcome.outcome is RegistryOutcome.QUARANTINED
    result = registry.publish(ref(), manifest, outcome)
    assert result.outcome is RegistryOutcome.QUARANTINED
    _assert_no_publication(registry)


def test_forged_evidence_does_not_publish(registry: PluginArtifactRegistry) -> None:
    """Сфабрикованное свидетельство отклоняется (печать verifier'а)."""
    result = registry.publish(ref(), valid_manifest(), {"verified": True})
    assert result.outcome is RegistryOutcome.QUARANTINED
    _assert_no_publication(registry)


def test_repeat_digest_with_other_manifest_is_artifact_digest_conflict(
    registry: PluginArtifactRegistry, verifier, store
) -> None:
    """Повторный digest с другим manifest → ARTIFACT_DIGEST_CONFLICT."""
    manifest_a = valid_manifest()
    first = _verified(verifier, manifest_a)
    assert registry.publish(ref(), manifest_a, first).outcome is RegistryOutcome.APPLIED

    manifest_b = valid_manifest(version="1.3.0", capabilities=["cache"])
    second = _verified(verifier, manifest_b)
    result = registry.publish(ref(), manifest_b, second)
    assert result.outcome is RegistryOutcome.QUARANTINED
    assert result.reason is RegistryReason.ARTIFACT_DIGEST_CONFLICT
    assert result.quarantine.metadata["artifact_error_code"] == "DIGEST_CONFLICT"
    assert DIGEST_CONFLICT_ERROR_CODE == "ARTIFACT_DIGEST_CONFLICT"
    # Текущая версия не изменилась.
    assert registry.current("mod_equipment").version == "1.2.0"


def test_idempotent_republish_same_digest(registry: PluginArtifactRegistry, verifier) -> None:
    manifest = valid_manifest()
    outcome = _verified(verifier, manifest)
    assert registry.publish(ref(), manifest, outcome).outcome is RegistryOutcome.APPLIED
    again = registry.publish(ref(), manifest, outcome)
    assert again.outcome is RegistryOutcome.CONFLICT
    assert again.reason is RegistryReason.VERSION_ALREADY_PUBLISHED


def test_incompatible_manifest_is_not_published(registry: PluginArtifactRegistry, verifier) -> None:
    """Неизвестная capability → INCOMPATIBLE, запись не публикуется."""
    manifest = valid_manifest(capabilities=["cache", "alien_capability"])
    outcome = verifier.verify(
        ref(), manifest, grant=full_grant(), scan_verified=True, csp_verified=True
    )
    assert outcome.outcome is RegistryOutcome.QUARANTINED
    result = registry.publish(ref(), manifest, outcome)
    assert result.outcome is RegistryOutcome.QUARANTINED
    _assert_no_publication(registry)
    assert COMPATIBILITY_ERROR_CODE == "INCOMPATIBLE"


def test_publish_snapshot_of_both_flags_works(registry: PluginArtifactRegistry, verifier) -> None:
    """Базовый publish(ref, manifest, evidence) берёт ОБА флага из evidence."""
    manifest = valid_manifest()
    outcome = _verified(verifier, manifest)
    assert outcome.evidence.scan_verified is True
    assert outcome.evidence.csp_verified is True
    assert registry.publish(ref(), manifest, outcome).outcome is RegistryOutcome.APPLIED


def test_publish_with_explicit_false_csp_is_quarantined(
    registry: PluginArtifactRegistry, verifier
) -> None:
    """Явный ``csp_verified=False`` в publish = принудительный отказ (fail-closed)."""
    manifest = valid_manifest()
    outcome = _verified(verifier, manifest)
    result = registry.publish(ref(), manifest, outcome, csp_verified=False)
    assert result.outcome is RegistryOutcome.QUARANTINED
    _assert_no_publication(registry)


def test_publish_evidence_without_csp_flag_is_quarantined(
    registry: PluginArtifactRegistry, verifier
) -> None:
    """Evidence без ``csp_verified`` не публикуется, даже если gate вызван вручную."""
    manifest = valid_manifest()
    outcome = _verified(verifier, manifest, csp_verified=False)
    assert outcome.outcome is RegistryOutcome.QUARANTINED  # verify сам отказал
    result = registry.publish(ref(), manifest, outcome)
    assert result.outcome is RegistryOutcome.QUARANTINED
    _assert_no_publication(registry)


def test_compatibility_error_code(registry: PluginArtifactRegistry) -> None:
    assert registry.compatibility_error_code() == "INCOMPATIBLE"
    assert COMPATIBILITY_ERROR_CODE == "INCOMPATIBLE"


def test_get_and_current(registry: PluginArtifactRegistry, verifier) -> None:
    manifest = valid_manifest()
    outcome = _verified(verifier, manifest)
    registry.publish(ref(), manifest, outcome)
    assert registry.get("mod_equipment", "1.2.0") is not None
    assert registry.get("mod_equipment", "9.9.9") is None
    assert registry.current("mod_equipment") is not None
    assert registry.current("unknown") is None


def test_quarantine_marks_version_and_clears_current(
    registry: PluginArtifactRegistry, verifier, store
) -> None:
    manifest = valid_manifest()
    outcome = _verified(verifier, manifest)
    registry.publish(ref(), manifest, outcome)
    result = registry.quarantine(ref(), manifest, reason="compromised signature", actor="admin")
    assert result.outcome is RegistryOutcome.QUARANTINED
    record = store.by_digest(DIGEST_A)
    assert record is not None
    assert record.status.value == "revoked"
    assert record.quarantine is result.quarantine
    assert registry.current("mod_equipment") is None


def test_rollback_to_unsigned_digest_is_invalid(registry: PluginArtifactRegistry, verifier, store) -> None:
    """Rollback на неподписанный digest → ROLLBACK_INVALID."""
    manifest_v1 = valid_manifest(version="1.1.0")
    manifest_v2 = valid_manifest(version="1.2.0")
    out_v1 = _verified(verifier, manifest_v1, digest=DIGEST_B)
    out_v2 = _verified(verifier, manifest_v2, digest=DIGEST_A)
    assert registry.publish(ref(DIGEST_B), manifest_v1, out_v1).outcome is RegistryOutcome.APPLIED
    assert registry.publish(ref(DIGEST_A), manifest_v2, out_v2).outcome is RegistryOutcome.APPLIED

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


def test_rollback_to_published_signed_digest(registry: PluginArtifactRegistry, verifier, store) -> None:
    """Успешный откат = деплой предыдущего immutable digest через RollbackCommand."""
    manifest_v1 = valid_manifest(version="1.1.0")
    manifest_v2 = valid_manifest(version="1.2.0")
    out_v1 = _verified(verifier, manifest_v1, digest=DIGEST_B)
    out_v2 = _verified(verifier, manifest_v2, digest=DIGEST_A)
    registry.publish(ref(DIGEST_B), manifest_v1, out_v1)
    registry.publish(ref(DIGEST_A), manifest_v2, out_v2)

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


def test_rollback_command_is_reused_not_copied(store, verifier, registry) -> None:
    """Реестр использует RollbackCommand из dynamic_app_registry (импорт)."""
    manifest_v1 = valid_manifest(version="1.1.0")
    manifest_v2 = valid_manifest(version="1.2.0")
    registry.publish(ref(DIGEST_B), manifest_v1, _verified(verifier, manifest_v1, DIGEST_B))
    registry.publish(ref(DIGEST_A), manifest_v2, _verified(verifier, manifest_v2, DIGEST_A))

    captured = []
    original = store.append_lifecycle

    def spy(*, command, actor, detail):
        captured.append(command)
        original(command=command, actor=actor, detail=detail)

    store.append_lifecycle = spy  # type: ignore[method-assign]
    registry.rollback("mod_equipment", DIGEST_B, actor="admin", reason="r", cosign_verified=True)
    assert isinstance(captured[0], RollbackCommand)
    assert captured[0].target_version_id == "1.1.0"
    assert captured[0].expected_artifact_digest == DIGEST_B
