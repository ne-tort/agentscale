"""Unit-тесты verification pipeline (§5.1, критерий 18).

Проверяется: cosign verify → digest → validate_manifest → compatibility gate
→ decision. Любая неудача → QUARANTINED + QuarantineEvidence, НЕ публикация.

Вариант A (аудит F11, блокер №5): валидный manifest содержит РОВНО 11 полей
закрытого whitelist задачи 01 (``capabilities``, ``frontend.entry/base_path``),
а ``scan_verified`` / ``csp_verified`` приходят ОТДЕЛЬНЫМИ аргументами из CI и
static-origin. ``requestedCapabilities`` / ``frontend.csp`` в манифесте НЕ
требуются и схемой ЗАПРЕЩЕНЫ.
"""

from __future__ import annotations

import pytest

from prodavan.application.dynamic_app_registry.contracts import (
    QuarantineEvidence,
    RegistryOutcome,
)
from prodavan.application.plugins.artifacts import (
    ArtifactVerifier,
    CompatibilityGate,
    VerificationResult,
)
from .conftest import (
    DIGEST_A,
    DIGEST_B,
    FakeCosign,
    FakeGrant,
    full_grant,
    pilot_manifest,
    real_validator,
    ref,
    valid_manifest,
)


# Оба внешних подтверждения для «счастливого» пути (вариант A).
VERIFIED = {"scan_verified": True, "csp_verified": True}


def _assert_quarantined(outcome, code: str) -> None:
    assert outcome.outcome is RegistryOutcome.QUARANTINED
    assert outcome.result.outcome is RegistryOutcome.QUARANTINED
    evidence = outcome.result.quarantine
    assert isinstance(evidence, QuarantineEvidence)
    assert evidence.metadata["artifact_error_code"] == code
    assert outcome.evidence.verified is False
    assert outcome.evidence.outcome is RegistryOutcome.QUARANTINED


# ────────────────────────── главный сценарий варианта A ──────────────────────────

def test_valid_manifest_with_both_verified_flags_is_applied(
    verifier: ArtifactVerifier, cosign: FakeCosign
) -> None:
    """Валидный 11-польный manifest + scan/csp_verified → APPLIED (блокер №5 снят)."""
    outcome = verifier.verify(ref(), valid_manifest(), grant=full_grant(), **VERIFIED)
    assert outcome.outcome is RegistryOutcome.APPLIED
    assert outcome.quarantine is None
    assert outcome.evidence.verified is True
    assert outcome.evidence.scan_verified is True
    assert outcome.evidence.csp_verified is True
    assert cosign.calls[0][0] == ref().image_ref()
    assert "@" in cosign.calls[0][0] and ":latest" not in cosign.calls[0][0]
    assert outcome.evidence.artifact_digest == DIGEST_A


def test_pilot_manifest_passes_real_validator_and_gate() -> None:
    """Реальный hello-plugin/manifest.json проходит validate_manifest И gate.

    Интеграционная проверка блока F11: один и тот же dict больше НЕ может
    провалиться на compatibility gate из-за отсутствующих в схеме полей.
    """
    manifest = pilot_manifest()
    # 1. Настоящий валидатор задачи 01 (закрытый whitelist, 11 полей).
    from prodavan.domain.plugins.validation import PluginValidationError

    try:
        real_validator()(manifest)
    except PluginValidationError as exc:  # fail-closed: такой расклад — ошибка фикстуры/контракта
        raise AssertionError(f"манифест пилота отклонён validate_manifest: {exc}") from exc
    # 2. В manifest нет запрещённых полей — gate их и не потребует.
    assert "requestedCapabilities" not in manifest
    assert "csp" not in manifest["frontend"]
    # 3. Gate с реальным грантом (все 9 capability пилота) + оба флага → ok.
    gate = CompatibilityGate(platform_version="4.0.0")
    verdict = gate.check(manifest, grant=full_grant(), **VERIFIED)
    assert verdict.ok is True
    assert verdict.code is None
    assert verdict.reason == ""


def test_pilot_manifest_end_to_end_with_real_validator(
    cosign: FakeCosign, clock
) -> None:
    """Полный pipeline на манифесте пилота с НАСТОЯЩИМ validate_manifest."""
    verifier = ArtifactVerifier(
        cosign,
        validator=real_validator(),
        identity="id",
        issuer="iss",
        compatibility=CompatibilityGate(platform_version="4.0.0"),
        clock=clock,
    )
    outcome = verifier.verify(ref(), pilot_manifest(), grant=full_grant(), **VERIFIED)
    assert outcome.outcome is RegistryOutcome.APPLIED
    assert outcome.evidence.verified is True


def test_forbidden_fields_are_not_required_by_gate() -> None:
    """``requestedCapabilities`` / ``frontend.csp`` больше НЕ требуются.

    Более того: они ЗАПРЕЩЕНЫ закрытым whitelist задачи 01, поэтому в валидном
    подписанном манифесте их нет — и gate всё равно даёт ok при обоих флагах.
    """
    manifest = valid_manifest()
    assert "requestedCapabilities" not in manifest
    assert "csp" not in manifest["frontend"]
    gate = CompatibilityGate(platform_version="4.0.0")
    assert gate.check(manifest, grant=full_grant(), **VERIFIED).ok is True


def test_manifest_with_forbidden_extra_field_is_rejected_by_gate() -> None:
    """Защита от рассинхрона: ``frontend.csp`` в manifest = отказ gate."""
    manifest = valid_manifest(frontend={"entry": "index.html", "base_path": "/", "csp": "default-src 'none'"})
    gate = CompatibilityGate(platform_version="4.0.0")
    verdict = gate.check(manifest, grant=full_grant(), **VERIFIED)
    assert verdict.ok is False
    assert verdict.code is not None
    assert "csp" in verdict.reason


# ────────────────────────── fail-closed флаги ──────────────────────────

def test_missing_csp_verified_is_incompatible(verifier: ArtifactVerifier) -> None:
    """Отсутствие ``csp_verified`` (default False) → INCOMPATIBLE."""
    outcome = verifier.verify(
        ref(), valid_manifest(), grant=full_grant(), scan_verified=True
    )
    _assert_quarantined(outcome, "INCOMPATIBLE")
    assert "csp_verified" in outcome.result.quarantine.metadata["detail"]


def test_explicit_csp_verified_false_is_incompatible(verifier: ArtifactVerifier) -> None:
    outcome = verifier.verify(
        ref(), valid_manifest(), grant=full_grant(), scan_verified=True, csp_verified=False
    )
    _assert_quarantined(outcome, "INCOMPATIBLE")


def test_non_bool_csp_verified_is_incompatible(verifier: ArtifactVerifier) -> None:
    """Fail-closed: не-bool (например строка) не трактуется как True."""
    outcome = verifier.verify(
        ref(), valid_manifest(), grant=full_grant(), scan_verified=True, csp_verified="yes"  # type: ignore[arg-type]
    )
    _assert_quarantined(outcome, "INCOMPATIBLE")


def test_missing_scan_is_incompatible(verifier: ArtifactVerifier) -> None:
    outcome = verifier.verify(
        ref(), valid_manifest(), grant=full_grant(), csp_verified=True
    )
    _assert_quarantined(outcome, "INCOMPATIBLE")
    assert "scan_verified" in outcome.result.quarantine.metadata["detail"]


@pytest.mark.parametrize("stage", ["scan", "csp"])
def test_gate_direct_denies_each_missing_flag(stage: str) -> None:
    gate = CompatibilityGate(platform_version="4.0.0")
    flags: dict[str, bool] = dict(VERIFIED)
    flags[f"{stage}_verified"] = False
    verdict = gate.check(valid_manifest(), grant=full_grant(), **flags)
    assert verdict.ok is False
    assert verdict.code is not None
    assert f"{stage}_verified" in verdict.reason


# ────────────────────────── сохранённые проверки pipeline ──────────────────────────

def test_cosign_verify_failed_is_quarantined(cosign: FakeCosign, validator, clock) -> None:
    """cosign verify упал → QUARANTINED, publish не происходит."""
    verifier = ArtifactVerifier(
        FakeCosign(verified=False),
        validator=validator,
        identity="id",
        issuer="iss",
        clock=clock,
    )
    outcome = verifier.verify(ref(), valid_manifest(), grant=full_grant(), **VERIFIED)
    _assert_quarantined(outcome, "UNSIGNED")


def test_cosign_exception_is_quarantined(cosign: FakeCosign, validator, clock) -> None:
    verifier = ArtifactVerifier(
        FakeCosign(raises=RuntimeError("cli missing")),
        validator=validator,
        identity="id",
        issuer="iss",
        clock=clock,
    )
    outcome = verifier.verify(ref(), valid_manifest(), grant=full_grant(), **VERIFIED)
    _assert_quarantined(outcome, "VERIFY_FAILED")


def test_missing_sbom_or_provenance_is_quarantined(validator, clock) -> None:
    for sbom, provenance in ((False, True), (True, False), (False, False)):
        verifier = ArtifactVerifier(
            FakeCosign(sbom=sbom, provenance=provenance),
            validator=validator,
            identity="id",
            issuer="iss",
            clock=clock,
        )
        outcome = verifier.verify(ref(), valid_manifest(), grant=full_grant(), **VERIFIED)
        _assert_quarantined(outcome, "VERIFY_FAILED")


def test_signed_digest_mismatch_is_quarantined(validator, clock) -> None:
    verifier = ArtifactVerifier(
        FakeCosign(verified=True, signed_digest=DIGEST_B),
        validator=validator,
        identity="id",
        issuer="iss",
        clock=clock,
    )
    outcome = verifier.verify(ref(DIGEST_A), valid_manifest(), grant=full_grant(), **VERIFIED)
    _assert_quarantined(outcome, "DIGEST_CONFLICT")


def test_self_reference_in_manifest_is_quarantined(verifier: ArtifactVerifier) -> None:
    outcome = verifier.verify(
        ref(),
        valid_manifest(artifact_digest="sha256:" + "a" * 64),
        grant=full_grant(),
        **VERIFIED,
    )
    _assert_quarantined(outcome, "DIGEST_CONFLICT")


def test_manifest_validator_rejection_is_quarantined(cosign: FakeCosign, clock) -> None:
    class Rejecting:
        def __call__(self, data):
            raise ValueError("PluginValidationError: bad manifest")

    verifier = ArtifactVerifier(
        cosign,
        validator=Rejecting(),
        identity="id",
        issuer="iss",
        clock=clock,
    )
    outcome = verifier.verify(ref(), valid_manifest(), grant=full_grant(), **VERIFIED)
    _assert_quarantined(outcome, "INCOMPATIBLE")


def test_unknown_capability_is_incompatible(verifier: ArtifactVerifier) -> None:
    """Неизвестная capability в ``capabilities`` → QUARANTINED."""
    manifest = valid_manifest(capabilities=["cache", "unknown_capability"])
    outcome = verifier.verify(ref(), manifest, grant=full_grant(), **VERIFIED)
    _assert_quarantined(outcome, "INCOMPATIBLE")


def test_capabilities_wider_than_grant_is_denied(verifier: ArtifactVerifier) -> None:
    """``capabilities`` шире CapabilityGrant.capabilities → отказ."""
    narrow = FakeGrant(("cache", "objects"))
    outcome = verifier.verify(ref(), valid_manifest(), grant=narrow, **VERIFIED)
    _assert_quarantined(outcome, "INCOMPATIBLE")
    assert "docs" in outcome.result.quarantine.metadata["detail"]


def test_missing_capabilities_field_is_denied(verifier: ArtifactVerifier) -> None:
    """Защита: без поля ``capabilities`` gate не «пропускает» по умолчанию."""
    manifest = valid_manifest()
    del manifest["capabilities"]
    outcome = verifier.verify(ref(), manifest, grant=full_grant(), **VERIFIED)
    _assert_quarantined(outcome, "INCOMPATIBLE")


@pytest.mark.parametrize("version", ["1.2", "v1.2.0", "1.2.0.3", "", "1.2.x", None])
def test_invalid_semver_is_rejected(verifier: ArtifactVerifier, version) -> None:
    outcome = verifier.verify(
        ref(), valid_manifest(version=version), grant=full_grant(), **VERIFIED
    )
    _assert_quarantined(outcome, "INCOMPATIBLE")


@pytest.mark.parametrize("protocol", ["1.0", "plugin-rpc/2", "", None])
def test_protocol_must_be_plugin_rpc_1(verifier: ArtifactVerifier, protocol) -> None:
    outcome = verifier.verify(
        ref(), valid_manifest(protocol=protocol), grant=full_grant(), **VERIFIED
    )
    _assert_quarantined(outcome, "INCOMPATIBLE")


def test_min_platform_version_above_platform_is_rejected(verifier: ArtifactVerifier) -> None:
    outcome = verifier.verify(
        ref(),
        valid_manifest(min_platform_version="5.0.0"),
        grant=full_grant(),
        **VERIFIED,
    )
    _assert_quarantined(outcome, "INCOMPATIBLE")


@pytest.mark.parametrize("min_version", ["4.0.0", "3.9.9", "1.0.0", "4.0.0-rc.1"])
def test_min_platform_version_at_or_below_platform_is_ok(
    verifier: ArtifactVerifier, min_version: str
) -> None:
    outcome = verifier.verify(
        ref(),
        valid_manifest(min_platform_version=min_version),
        grant=full_grant(),
        **VERIFIED,
    )
    assert outcome.outcome is RegistryOutcome.APPLIED


def test_broken_frontend_structure_is_incompatible(verifier: ArtifactVerifier) -> None:
    """Структура ``frontend`` проверяется по схеме: ровно entry + base_path."""
    for frontend in (
        {"entry": "index.html"},                                   # нет base_path
        {"entry": "index.html", "base_path": "/", "csp": "x"},   # лишнее поле
        {"entry": "", "base_path": "/"},                          # пустая строка
        "index.html",                                             # не объект
        None,
    ):
        manifest = valid_manifest(frontend=frontend)
        outcome = verifier.verify(ref(), manifest, grant=full_grant(), **VERIFIED)
        _assert_quarantined(outcome, "INCOMPATIBLE")


# ────────────────────────── прямой gate + evidence ──────────────────────────

def test_compatibility_gate_direct_checks() -> None:
    gate = CompatibilityGate(platform_version="4.0.0")
    assert gate.check(valid_manifest(), grant=full_grant(), **VERIFIED).ok is True
    verdict = gate.check(
        valid_manifest(capabilities=["nope"]),
        grant=full_grant(),
        **VERIFIED,
    )
    assert verdict.ok is False
    assert verdict.code is not None
    assert verdict.reason


def test_recheck_compatibility_threads_csp_verified(verifier: ArtifactVerifier) -> None:
    """``recheck_compatibility`` обязан прокидывать оба флага, иначе publish упадёт."""
    assert verifier.recheck_compatibility(
        valid_manifest(), grant=full_grant(), **VERIFIED
    ).ok is True
    assert verifier.recheck_compatibility(
        valid_manifest(), grant=full_grant(), scan_verified=True
    ).ok is False


def test_verified_result_requires_digest() -> None:
    with pytest.raises(ValueError):
        VerificationResult(verified=True, signed_digest=None)


def test_validate_evidence_rejects_foreign_verifier(verifier: ArtifactVerifier, cosign, validator, clock) -> None:
    from prodavan.application.plugins.artifacts import ArtifactError

    other = ArtifactVerifier(
        cosign, validator=validator, identity="id", issuer="iss", clock=clock
    )
    outcome = other.verify(ref(), valid_manifest(), grant=full_grant(), **VERIFIED)
    with pytest.raises(ArtifactError):
        verifier.validate_evidence(outcome.evidence, ref=ref(), manifest=valid_manifest())


def test_validate_evidence_rejects_changed_manifest(verifier: ArtifactVerifier) -> None:
    from prodavan.application.plugins.artifacts import ArtifactError

    outcome = verifier.verify(ref(), valid_manifest(), grant=full_grant(), **VERIFIED)
    with pytest.raises(ArtifactError):
        verifier.validate_evidence(
            outcome.evidence, ref=ref(), manifest=valid_manifest(version="9.9.9")
        )
