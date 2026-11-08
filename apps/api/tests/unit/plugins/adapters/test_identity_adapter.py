"""Тесты ``identity_adapter``: async-обёртка revocation + mint (НЕ запускать).

Покрытие по заданию:
* каждый адаптер транслирует вызов корректно;
* async-обёртка revocation возвращает bool = ПОДТВЕРЖДЕНИЕ записи;
* ``reason`` не теряется, но и не попадает в токен;
* mint заполняет ``jti``/``issued_at``/``expires_at`` из проверенного payload;
* коллизия имён ``MintPort``/``RevocationPort`` разрешена алиасами;
* fail-closed: пустой grant_id, unbound port, битый токен.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from prodavan.application.plugins.admin.contracts import (
    PLUGIN_CLAIMS_TTL_SECONDS,
    MintRequest,
)
from prodavan.application.plugins.admin.ports import (
    MintPort as AdminMintPort,
)
from prodavan.application.plugins.admin.ports import (
    RevocationPort as AdminRevocationPort,
)
from prodavan.application.plugins.adapters.identity_adapter import (
    AdminMintPort as ReExportedAdminMintPort,
    AdminRevocationPort as ReExportedAdminRevocationPort,
    IdentityMintAdapter,
    IdentityRevocationAdapter,
    IdentityRevocationPort,
)
from prodavan.application.plugins.identity.keys import keyring_from_secret
from prodavan.application.plugins.identity.mint import verify_plugin_token
from prodavan.application.plugins.identity.revocation import (
    InMemoryRevocation,
    RevocationPort as RealIdentityRevocationPort,
)
from prodavan.application.plugins.identity.types import PluginTokenError

from .fakes import COMPANY, DIGEST, INSTANCE, PLUGIN, SECRET, FakeSyncRevocation


def _mint_request() -> MintRequest:
    return MintRequest(
        company_id=COMPANY,
        plugin_id=PLUGIN,
        plugin_instance_id=INSTANCE,
        manifest_version="1.2.0",
        artifact_digest=DIGEST,
        grant_id="grant_001",
        scopes=("db.direct", "cache", "objects"),
        quota_ref=f"{COMPANY}/plan_default",
        ttl_seconds=PLUGIN_CLAIMS_TTL_SECONDS,
    )


# --------------------------------------------------------------------------- алиасы


def test_mint_port_alias_collision_is_resolved() -> None:
    """``admin.MintPort`` и ``activation.MintPort`` — РАЗНЫЕ протоколы.

    Проверяем, что алиасы в адаптерах указывают на правильные объекты и не
    перепутаны: у admin — ``mint(request=...)``, у activator — ``mint(
    plugin_instance_id=..., reason=...)``.
    """

    from prodavan.application.plugins.activation.ports import (
        MintPort as ActivatorMintPort,
    )
    from prodavan.application.plugins.adapters.activation_adapter import (
        ActivatorMintPort as ReExportedActivatorMintPort,
    )

    assert ReExportedAdminMintPort is AdminMintPort
    assert ReExportedAdminRevocationPort is AdminRevocationPort
    assert ReExportedActivatorMintPort is ActivatorMintPort
    assert ActivatorMintPort is not AdminMintPort
    assert IdentityRevocationPort is RealIdentityRevocationPort
    assert IdentityRevocationPort is not AdminRevocationPort
    # Имена методов в двух MintPort действительно разные.
    assert "request" in AdminMintPort.mint.__annotations__
    assert "plugin_instance_id" in ActivatorMintPort.mint.__annotations__


# --------------------------------------------------------------------------- revocation


async def test_revocation_adapter_wraps_sync_revoke_and_returns_bool() -> None:
    """Sync ``revoke(grant_id)`` → async ``revoke(*, grant_id, reason) -> bool``."""

    source = FakeSyncRevocation()
    adapter = IdentityRevocationAdapter(source)

    result = await adapter.revoke(grant_id="grant_001", reason="admin revoke")

    assert result is True  # не «вызов не упал», а подтверждение записи
    assert any(c.method == "revoke" and c.args == ("grant_001",) for c in source.calls)
    assert adapter.reasons == {"grant_001": "admin revoke"}


async def test_revocation_bool_is_false_when_write_not_confirmed() -> None:
    """Запись не подтверждена реестром → ``False`` (сервис даст REVOKE_FAILED)."""

    source = FakeSyncRevocation()
    # После revoke реестр по-прежнему сообщает «активен» → не подтверждено.
    source.active_override["grant_001"] = True
    adapter = IdentityRevocationAdapter(source)

    assert await adapter.revoke(grant_id="grant_001", reason="admin revoke") is False


async def test_revocation_is_idempotent() -> None:
    source = InMemoryRevocation()
    adapter = IdentityRevocationAdapter(source)

    assert await adapter.revoke(grant_id="grant_001", reason="first") is True
    assert await adapter.revoke(grant_id="grant_001", reason="repeat") is True
    assert await adapter.is_revoked(grant_id="grant_001") is True
    assert adapter.reasons["grant_001"] == "repeat"


async def test_revocation_is_revoked_fail_closed_on_empty_id() -> None:
    adapter = IdentityRevocationAdapter(InMemoryRevocation())
    assert await adapter.is_revoked(grant_id="") is True
    with pytest.raises(ValueError, match="grant_id"):
        await adapter.revoke(grant_id="", reason="x")


async def test_revocation_unbound_grant_revocation_fails_loudly() -> None:
    """``GrantRevocation`` без порта: is_revoked=True, но revoke — явный отказ."""

    from prodavan.application.plugins.identity.revocation import GrantRevocation

    adapter = IdentityRevocationAdapter(GrantRevocation(port=None))
    assert await adapter.is_revoked(grant_id="grant_001") is True
    with pytest.raises(PluginTokenError):
        await adapter.revoke(grant_id="grant_001", reason="admin revoke")


def test_revocation_adapter_requires_source() -> None:
    with pytest.raises(ValueError, match="источник отзывов"):
        IdentityRevocationAdapter(None)  # type: ignore[arg-type]


# --------------------------------------------------------------------------- mint


async def test_mint_adapter_translates_request_and_fills_jti_exp() -> None:
    """MintRequest → mint_plugin_claims → IssuedPluginClaims из payload."""

    adapter = IdentityMintAdapter(secret=SECRET)
    issued = await adapter.mint(request=_mint_request())

    assert issued.grant_id == "grant_001"
    assert issued.ttl_seconds == PLUGIN_CLAIMS_TTL_SECONDS
    assert issued.jti and issued.token
    assert issued.expires_at.tzinfo is not None
    assert issued.expires_at > datetime.now(UTC)

    # Тот же токен проверяется настоящим verify_plugin_token: подпись, iss/aud,
    # typ, exp, jti, sub, artifact_digest — всё из проверенного payload.
    claims = verify_plugin_token(
        issued.token, keyring=keyring_from_secret(SECRET)
    )
    assert claims.jti == issued.jti
    assert claims.grant_id == issued.grant_id
    assert claims.plugin_instance_id == INSTANCE
    assert claims.company_id == COMPANY
    assert claims.artifact_digest == DIGEST
    assert claims.scopes == ("db.direct", "cache", "objects")
    # §4.2: в коротком токене НЕТ egress/квот.
    assert "egress" not in claims.__dict__
    assert "quotas" not in claims.__dict__


async def test_mint_adapter_rejects_empty_secret() -> None:
    with pytest.raises(ValueError, match="secret"):
        IdentityMintAdapter(secret="")


async def test_mint_adapter_rejects_non_mint_request() -> None:
    adapter = IdentityMintAdapter(secret=SECRET)
    with pytest.raises(TypeError, match="MintRequest"):
        await adapter.mint(request={"grant_id": "grant_001"})  # type: ignore[arg-type]


def test_issued_claims_contract_rejects_bad_expiry() -> None:
    """``IssuedPluginClaims`` сам fail-closed: naive datetime запрещён."""

    from prodavan.application.plugins.admin.contracts import IssuedPluginClaims

    with pytest.raises(ValueError, match="timezone-aware"):
        IssuedPluginClaims(
            token="t",
            grant_id="g",
            jti="j",
            expires_at=datetime(2026, 11, 8),  # naive
            ttl_seconds=300,
        )
