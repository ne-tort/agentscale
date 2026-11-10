"""Тесты ``identity_adapter``: async-обёртка revocation + mint.

Покрытие по заданию:
* каждый адаптер транслирует вызов корректно;
* canonical async revocation await'ится и возвращает bool = ПОДТВЕРЖДЕНИЕ записи;
* AST-regression запрещает не-awaited source.revoke/is_revoked/is_active;
* ``reason`` не теряется, но и не попадает в токен;
* mint заполняет ``jti``/``expires_at`` из проверенного payload (``issued_at``
  восстанавливается как ``expires_at - ttl_seconds`` — отдельного поля в
  ``IssuedPluginClaims`` задачи 12 нет);
* коллизия имён ``MintPort``/``RevocationPort`` разрешена алиасами;
* fail-closed: пустой grant_id, unbound port, битый токен.
"""

from __future__ import annotations

import ast
import inspect
from datetime import UTC, datetime, timedelta

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

from .fakes import COMPANY, DIGEST, INSTANCE, PLUGIN, SECRET, FakeAsyncRevocation


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


async def test_revocation_adapter_awaits_async_revoke_and_returns_bool() -> None:
    """Async ``revoke(grant_id) -> bool`` сохраняет typed success и reason."""

    source = FakeAsyncRevocation()
    adapter = IdentityRevocationAdapter(source)

    result = await adapter.revoke(grant_id="grant_001", reason="admin revoke")

    assert result is True
    assert any(c.method == "revoke" and c.args == ("grant_001",) for c in source.calls)
    assert any(c.method == "is_revoked" for c in source.calls)
    assert adapter.reasons == {"grant_001": "admin revoke"}


async def test_revocation_bool_is_false_when_source_rejects_write() -> None:
    """Даже отозванное состояние не маскирует typed ``revoke=False`` источника."""

    source = FakeAsyncRevocation(revoke_result=False)
    adapter = IdentityRevocationAdapter(source)

    assert await adapter.revoke(grant_id="grant_001", reason="admin revoke") is False
    assert source.revoked == {"grant_001"}


async def test_revocation_bool_is_false_when_write_not_confirmed() -> None:
    """Запись не подтверждена реестром → ``False`` (сервис даст REVOKE_FAILED)."""

    source = FakeAsyncRevocation()
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


async def test_revocation_adapter_awaits_is_revoked_for_both_states() -> None:
    source = FakeAsyncRevocation()
    adapter = IdentityRevocationAdapter(source)

    assert await adapter.is_revoked(grant_id="grant_001") is False
    source.revoked.add("grant_001")
    assert await adapter.is_revoked(grant_id="grant_001") is True
    assert [c.method for c in source.calls if c.method == "is_revoked"] == [
        "is_revoked",
        "is_revoked",
    ]


class _ActiveOnlyRevocation:
    """Минимальный canonical порт без необязательного ``is_revoked``."""

    def __init__(self) -> None:
        self.revoked: set[str] = set()
        self.active_calls: list[datetime | None] = []

    async def revoke(self, grant_id: str) -> bool:
        self.revoked.add(grant_id)
        return True

    async def is_active(self, grant_id: str, *, now: datetime | None = None) -> bool:
        self.active_calls.append(now)
        return grant_id not in self.revoked


async def test_revocation_adapter_awaits_is_active_fallback() -> None:
    source = _ActiveOnlyRevocation()
    adapter = IdentityRevocationAdapter(source)

    assert await adapter.is_revoked(grant_id="grant_001") is False
    assert await adapter.revoke(grant_id="grant_001", reason="admin revoke") is True
    assert await adapter.is_revoked(grant_id="grant_001") is True
    assert len(source.active_calls) == 3
    assert all(
        moment is not None and moment.tzinfo is not None
        for moment in source.active_calls
    )


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


def test_revocation_adapter_statically_awaits_every_source_call() -> None:
    """Запрещаем возврат к coroutine-to-bool и unawaited coroutine warnings."""

    tree = ast.parse(inspect.getsource(IdentityRevocationAdapter))
    parents = {
        child: parent
        for parent in ast.walk(tree)
        for child in ast.iter_child_nodes(parent)
    }
    checked: set[str] = set()
    method_names = {"revoke", "is_revoked", "is_active"}

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        function = node.func
        if isinstance(function, ast.Attribute):
            method_name = function.attr
            is_source_call = (
                isinstance(function.value, ast.Attribute)
                and function.value.attr == "_source"
            )
        elif isinstance(function, ast.Name):
            method_name = function.id
            # Ветка optional is_revoked вызывает bound async method через local.
            is_source_call = function.id == "is_revoked"
        else:
            continue
        if method_name not in method_names or not is_source_call:
            continue
        assert isinstance(parents[node], ast.Await), (
            f"source.{method_name}() должен быть непосредственным аргументом await"
        )
        checked.add(method_name)

    assert checked == method_names


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
    # Момент выпуска восстанавливается из проверенного exp и TTL (в контракте
    # задачи 12 отдельного issued_at нет).
    issued_at = issued.expires_at - timedelta(seconds=issued.ttl_seconds)
    assert issued_at <= datetime.now(UTC) + timedelta(seconds=1)

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
    # §4.2: в коротком токене НЕТ egress/квот (dataclass slots — проверяем
    # по ``__slots__``, т.к. ``__dict__`` у slots-класса отсутствует).
    assert "egress" not in type(claims).__slots__
    assert "quotas" not in type(claims).__slots__


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
