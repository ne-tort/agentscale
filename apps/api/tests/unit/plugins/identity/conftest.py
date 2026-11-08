"""Общие фикстуры для unit-тестов trust-контура плагинов (F1)."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

import pytest

from prodavan.application.plugins.identity import (
    KeyRing,
    PluginClaims,
    PluginSurfaceAllowlist,
    keyring_from_secret,
    mint_plugin_claims,
    verify_plugin_token,
)

TEST_SECRET = "plugin-identity-test-secret-explicit-9f3c"

# Валидный digest из док-примера ``sha256:<64 lowercase hex>``.
TEST_DIGEST = "sha256:" + ("a1b2c3d4" * 8)


@dataclass(frozen=True)
class FakeGrant:
    """Тестовая замена доменного ``CapabilityGrant`` (Protocol из mint.py)."""

    grant_id: str = "grant_01"
    plugin_id: str = "mod_equipment"
    company_id: str = "co_123"
    scopes: tuple[str, ...] = ("cache", "objects", "events")
    manifest_version: str = "1.2.0"
    artifact_digest: str = TEST_DIGEST
    quota_ref: str = "co_123/plan_pro"

    def replace(self, **changes: Any) -> "FakeGrant":
        data = {**self.__dict__, **changes}
        return FakeGrant(**data)


@dataclass(frozen=True)
class FakeInstance:
    """Тестовая замена ``PluginInstance``."""

    plugin_instance_id: str = "inst_abc"
    cabinet_id: str | None = "cab_456"
    project_id: str | None = None


@pytest.fixture
def secret() -> str:
    return TEST_SECRET


@pytest.fixture
def grant() -> FakeGrant:
    return FakeGrant()


@pytest.fixture
def instance() -> FakeInstance:
    return FakeInstance()


@pytest.fixture
def keyring(secret: str) -> KeyRing:
    return keyring_from_secret(secret)


@pytest.fixture
def now() -> int:
    return int(time.time())


@pytest.fixture
def claims(
    grant: FakeGrant,
    instance: FakeInstance,
    secret: str,
    keyring: KeyRing,
    now: int,
) -> PluginClaims:
    """Полный round-trip: mint → verify с фиксированным ``now``."""
    token = mint_plugin_claims(grant=grant, instance=instance, secret=secret)
    return verify_plugin_token(token, keyring=keyring, now=now)


@pytest.fixture
def allowlist() -> PluginSurfaceAllowlist:
    return PluginSurfaceAllowlist()
