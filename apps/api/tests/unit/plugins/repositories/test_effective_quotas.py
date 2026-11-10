"""Read-only effective quotas persisted-grant contract."""

from __future__ import annotations

import pytest

from prodavan.application.plugins.admin.contracts import QUOTA_KEYS
from prodavan.infrastructure.persistence.models.plugin_platform import CapabilityGrantRow
from prodavan.infrastructure.persistence.repositories.plugins._schema import (
    PluginRepositorySchemaError,
    PluginRepositoryUnavailable,
)
from prodavan.infrastructure.persistence.repositories.plugins.grants import (
    SqlCapabilityGrantRepository,
)

from .conftest import NOW, FakeSession, FakeSessionFactory, grant_row

QUOTAS = {
    "ops_per_minute": 17,
    "max_rows": 23,
    "max_bytes": 1_001,
    "max_keys": 37,
    "max_object_bytes": 2_003,
    "max_egress_bytes": 3_001,
}


def _repository(session: FakeSession) -> SqlCapabilityGrantRepository:
    return SqlCapabilityGrantRepository(
        session_factory=FakeSessionFactory(session), now=lambda: NOW
    )


def _use_grant_row_model(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "prodavan.infrastructure.persistence.repositories.plugins.grants.load_row",
        lambda _name: CapabilityGrantRow,
    )


async def test_exact_six_quotas_roundtrip_from_persisted_grant(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _use_grant_row_model(monkeypatch)
    repository = _repository(FakeSession(rows=[grant_row(quota=dict(QUOTAS))]))

    records = await repository.list_by_company_async(company_id="co_123")

    assert len(records) == 1
    record = records[0]
    assert {name: getattr(record.quotas, name) for name in QUOTA_KEYS} == QUOTAS
    persisted = repository._row_values(record, CapabilityGrantRow)["quota"]
    assert persisted == QUOTAS
    assert set(persisted) == set(QUOTA_KEYS)


async def test_scoped_grant_reader_returns_none_for_missing_grant(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _use_grant_row_model(monkeypatch)
    repository = _repository(FakeSession(rows=[]))

    assert await repository.get(company_id="co_123", plugin_id="mod_equipment") is None


@pytest.mark.parametrize(
    "overrides",
    [
        pytest.param({"company_id": "co_other"}, id="company-mismatch"),
        pytest.param({"plugin_id": "mod_other"}, id="plugin-mismatch"),
    ],
)
async def test_scoped_grant_reader_rejects_company_plugin_mismatch(
    monkeypatch: pytest.MonkeyPatch, overrides: dict[str, str]
) -> None:
    _use_grant_row_model(monkeypatch)
    # FakeSession намеренно возвращает строку без SQL filtering: postcondition
    # репозитория обязан сам отклонить нарушенный tenant/plugin binding.
    repository = _repository(FakeSession(rows=[grant_row(**overrides)]))

    with pytest.raises(PluginRepositoryUnavailable, match="binding"):
        await repository.get(company_id="co_123", plugin_id="mod_equipment")


@pytest.mark.parametrize("missing_key", QUOTA_KEYS)
def test_incomplete_persisted_quotas_are_typed_schema_error(missing_key: str) -> None:
    raw = dict(QUOTAS)
    raw.pop(missing_key)
    repository = _repository(FakeSession())

    with pytest.raises(PluginRepositorySchemaError, match="effective quotas"):
        repository.to_record(grant_row(quota=raw))


@pytest.mark.parametrize("quota_key", QUOTA_KEYS)
@pytest.mark.parametrize("invalid", [0, -1, True, "60"])
def test_invalid_persisted_quota_values_are_not_coerced_or_defaulted(
    quota_key: str, invalid: object
) -> None:
    raw = dict(QUOTAS)
    raw[quota_key] = invalid
    repository = _repository(FakeSession())

    with pytest.raises(PluginRepositorySchemaError, match=quota_key):
        repository.to_record(grant_row(quota=raw))


def test_absent_persisted_quota_object_is_not_replaced_with_defaults() -> None:
    repository = _repository(FakeSession())

    with pytest.raises(PluginRepositorySchemaError, match="effective quotas"):
        repository.to_record(grant_row(quota=None))
