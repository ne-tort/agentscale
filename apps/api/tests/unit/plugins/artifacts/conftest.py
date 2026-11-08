"""Общие фикстуры для unit-тестов зоны артефактов плагинов.

Тесты НЕ запускаются (verification freeze); фикстуры детерминированы: фиксированный
digest, фиксированные часы, in-memory cosign/validator/grant — никаких сетевых
вызовов, кластера или БД.
"""

from __future__ import annotations

from datetime import UTC, datetime
import json
from pathlib import Path
from typing import Any

import pytest

from prodavan.application.plugins.artifacts import (
    ArtifactVerifier,
    CompatibilityGate,
    CosignPort,
    InMemoryRegistryStore,
    OciArtifactRef,
    PluginArtifactRegistry,
    VerificationResult,
)

# Фиксированные digest'ы: ровно 64 hex в нижнем регистре.
DIGEST_A = "sha256:" + "a" * 64
DIGEST_B = "sha256:" + "b" * 64
DIGEST_UNSIGNED = "sha256:" + "c" * 64
LAYER_DIGEST = "sha256:" + "d" * 64
CONFIG_DIGEST = "sha256:" + "e" * 64

IDENTITY = "https://github.com/ne-tort/agentscale/.github/workflows/plugin-artifacts.yml@refs/heads/main"
ISSUER = "https://token.actions.githubusercontent.com"

FIXED_TIME = datetime(2026, 11, 8, 9, 0, 0, tzinfo=UTC)


class FakeCosign(CosignPort):
    """Детерминированный cosign-порт без CLI/сети."""

    def __init__(
        self,
        *,
        verified: bool = True,
        signed_digest: str | None = None,
        sbom: bool = True,
        provenance: bool = True,
        raises: Exception | None = None,
    ) -> None:
        # По умолчанию cosign «подтверждает» ровно тот digest, который ему
        # передали в image_ref (как реальный cosign verify). Явно заданный
        # signed_digest нужен только для теста расхождения digest.
        self.verified = verified
        self.signed_digest = signed_digest
        self.sbom = sbom
        self.provenance = provenance
        self.raises = raises
        self.calls: list[tuple[str, str, str]] = []

    def verify(self, image_ref: str, identity: str, issuer: str) -> VerificationResult:
        self.calls.append((image_ref, identity, issuer))
        if self.raises is not None:
            raise self.raises
        digest = self.signed_digest
        if digest is None and "@" in image_ref:
            digest = image_ref.split("@", 1)[1]
        return VerificationResult(
            verified=self.verified,
            signed_digest=digest,
            identity=identity,
            issuer=issuer,
            sbom_verified=self.sbom,
            provenance_verified=self.provenance,
        )


class AcceptingValidator:
    """Валидатор задачи 01 (позитивный путь): не бросает, возвращает None."""

    def __init__(self, *, error: Exception | None = None) -> None:
        self.error = error
        self.calls: list[dict[str, Any]] = []

    def __call__(self, data: dict[str, Any]) -> object:
        self.calls.append(dict(data))
        if self.error is not None:
            raise self.error
        return None


class FakeGrant:
    """Структурный ``CapabilityGrant`` задачи 01: поле ``capabilities``."""

    def __init__(self, capabilities: tuple[str, ...]) -> None:
        self.capabilities = capabilities


def valid_manifest(**overrides: Any) -> dict[str, Any]:
    """Подписанный plugin-manifest v1, проходящий ``validate_manifest`` задачи 01.

    Вариант A (аудит F11, блокер №5): РОВНО 11 полей закрытого whitelist из
    ``MANIFEST_ALLOWED_FIELDS``. НЕТ запрещённых схемой ``requestedCapabilities``
    и ``frontend.csp``: запрошенные capability живут в поле ``capabilities``,
    а подтверждения сканирования/CSP приходят ОТДЕЛЬНЫМИ аргументами gate
    (``scan_verified`` / ``csp_verified``) из CI и static-origin.
    """
    manifest: dict[str, Any] = {
        "plugin_id": "mod_equipment",
        "version": "1.2.0",
        "protocol": "plugin-rpc/1",
        "frontend": {"entry": "index.html", "base_path": "/"},
        "backend": {"port": 8080, "health": "/health"},
        "capabilities": ["cache", "objects", "docs"],
        "migrations": {"engine": "liquibase", "path": "migrations/liquibase/changelog.xml"},
        "egress_ref": "grant:mod_equipment",
        "public_pages": [],
        "webhooks": ["/webhook/external-system"],
        "min_platform_version": "4.0.0",
    }
    manifest.update(overrides)
    return manifest


def pilot_manifest() -> dict[str, Any]:
    """Реальный manifest пилота ``packages/plugins/hello-plugin/manifest.json``.

    Читается КАК ДАННЫЕ (без импорта приложения): это тот самый dict, который
    проходит ``validate_manifest`` и ДОЛЖЕН давать ``CompatibilityVerdict(ok=True)``
    при обоих verified-флагах. Используется в интеграционном тесте gate.
    """
    # conftest.py -> artifacts(0), plugins(1), unit(2), tests(3), api(4),
    # apps(5), root(6)
    root = Path(__file__).resolve().parents[6]
    path = root / "packages" / "plugins" / "hello-plugin" / "manifest.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(payload, dict), "манифест пилота обязан быть объектом"
    return payload


def real_validator() -> object:
    """Настоящий ``validate_manifest`` задачи 01 (ленивый импорт).

    Проверяет, что фикстура/манифест пилота действительно проходят закрытый
    whitelist, а не только «принимающий» валидатор-заглушку.
    """
    from prodavan.domain.plugins.validation import validate_manifest

    return validate_manifest


def ref(digest: str = DIGEST_A) -> OciArtifactRef:
    return OciArtifactRef(
        registry="ghcr.io",
        repository="ne-tort/plugins/mod_equipment",
        digest=digest,
    )


def full_grant() -> FakeGrant:
    return FakeGrant(("cache", "objects", "docs", "events", "jobs", "pods", "mcp", "secrets", "network"))


@pytest.fixture
def cosign() -> FakeCosign:
    return FakeCosign()


@pytest.fixture
def validator() -> AcceptingValidator:
    return AcceptingValidator()


@pytest.fixture
def grant() -> FakeGrant:
    return full_grant()


@pytest.fixture
def clock():
    return lambda: FIXED_TIME


@pytest.fixture
def verifier(cosign: FakeCosign, validator: AcceptingValidator, clock) -> ArtifactVerifier:
    return ArtifactVerifier(
        cosign,
        validator=validator,
        identity=IDENTITY,
        issuer=ISSUER,
        compatibility=CompatibilityGate(platform_version="4.0.0"),
        clock=clock,
    )


@pytest.fixture
def store() -> InMemoryRegistryStore:
    return InMemoryRegistryStore()


@pytest.fixture
def registry(verifier: ArtifactVerifier, store: InMemoryRegistryStore, clock) -> PluginArtifactRegistry:
    return PluginArtifactRegistry(store=store, verifier=verifier, clock=clock)


def backend_layer() -> dict[str, Any]:
    return {
        "digest": LAYER_DIGEST,
        "size": 1024,
        "scanned": True,
        "architecture": "linux/amd64",
    }


def frontend_layer() -> dict[str, Any]:
    return {
        "digest": LAYER_DIGEST,
        "size": 512,
        "csp": "default-src 'none'; script-src 'self'",
        "entry": "index.html",
    }


def migrations_layer() -> dict[str, Any]:
    return {"digest": LAYER_DIGEST, "size": 256, "engine": "liquibase"}


def config_descriptor() -> dict[str, Any]:
    return {"digest": CONFIG_DIGEST, "size": 128}


__all__ = [
    "CONFIG_DIGEST",
    "DIGEST_A",
    "DIGEST_B",
    "DIGEST_UNSIGNED",
    "FIXED_TIME",
    "IDENTITY",
    "ISSUER",
    "LAYER_DIGEST",
    "AcceptingValidator",
    "FakeCosign",
    "FakeGrant",
    "backend_layer",
    "config_descriptor",
    "frontend_layer",
    "full_grant",
    "migrations_layer",
    "pilot_manifest",
    "real_validator",
    "ref",
    "valid_manifest",
]
