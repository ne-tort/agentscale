"""Общие фикстуры F3-admin: собранный Harness и PUBLISHED-артефакт по умолчанию."""

from __future__ import annotations

import pytest

from prodavan.application.plugins.admin.contracts import (
    ArtifactPublicationStatus,
    CapabilitySet,
)

from .fakes import (
    DIGEST_A,
    PLUGIN_X,
    Harness,
)


@pytest.fixture
def harness() -> Harness:
    """Полностью подключённые in-memory порты + сервис."""

    return Harness()


@pytest.fixture
def published(harness: Harness) -> str:
    """Засеять реестр PUBLISHED-артефактом и вернуть его digest."""

    harness.artifact_registry.seed(
        plugin_id=PLUGIN_X,
        manifest_version="1.2.0",
        artifact_digest=DIGEST_A,
        status=ArtifactPublicationStatus.PUBLISHED,
    )
    return DIGEST_A


@pytest.fixture
def capabilities() -> CapabilitySet:
    return CapabilitySet(capabilities=("db.direct", "cache", "objects"))


@pytest.fixture
def grant_args(published: str, capabilities: CapabilitySet, harness: Harness) -> dict:
    harness.publication_approvals.seed(
        company_id="co_a",
        plugin_id=PLUGIN_X,
        manifest_version="1.2.0",
        artifact_digest=published,
        capabilities=capabilities,
    )
    return {
        "company_id": "co_a",
        "plugin_id": PLUGIN_X,
        "manifest_version": "1.2.0",
        "artifact_digest": published,
        "capabilities": capabilities,
    }
