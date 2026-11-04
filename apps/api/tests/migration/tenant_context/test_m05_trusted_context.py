"""Acceptance for the M05 ``TrustedTenantContextPort`` implementation; authored, not run.

The M05 port differs from M04: it accepts raw identities (tenant/subject/session) plus a
``trusted`` flag instead of a server-derived context object.  These tests prove that the
adapter treats all three identities strictly as assertions, writes only the resolved
values, binds tenant-wide (the M05 runtime context has no cabinet/project), attests the
database transaction it is bound to, and restores the previous state on ``clear``.
"""

from __future__ import annotations

import dataclasses
import inspect

import pytest

from prodavan.application.migration.m05_instance_runtime.instance_persistence import (
    M05RlsBinding,
    TrustedTenantContextPort,
)
from prodavan.application.migration.tenant_context import (
    M05_RUNTIME_RELATIONS,
    M05_SCOPE_CONTRACT_GAP,
    RlsTrustedTenantContextAdapter,
)
from prodavan.infrastructure.persistence.rls.context import RlsContextError
from prodavan.infrastructure.persistence.rls.policies import TRUSTED_SERVICE_ROLE

from tests.migration.tenant_context.postgres_double import (
    CORRELATION,
    CORRELATION_OTHER,
    SESSION_A,
    SUBJECT_A,
    TENANT_A,
    TENANT_B,
    FakeAsyncSession,
    FakePostgres,
    FakeSessionFactory,
    StubResolver,
    build_principal,
)


def adapter(resolver: StubResolver | None = None) -> RlsTrustedTenantContextAdapter:
    return RlsTrustedTenantContextAdapter(
        principal_resolver=resolver if resolver is not None else StubResolver(build_principal())
    )


def reason(exc_info: pytest.ExceptionInfo[RlsContextError]) -> str:
    return exc_info.value.reason_code


async def _bind(port, session, **overrides):
    values = {
        "tenant_id": TENANT_A,
        "subject_id": SUBJECT_A,
        "session_id": SESSION_A,
        "correlation_id": CORRELATION,
        "trusted": False,
    }
    values.update(overrides)
    return await port.bind(session=session, **values)


# ---------------------------------------------------------------------------
# Positive path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_bind_applies_the_resolved_tenant_and_attests_transaction() -> None:
    pg = FakePostgres()
    session = FakeAsyncSession(pg)
    port = adapter()

    async with session.begin():
        binding = await _bind(port, session)

        assert isinstance(binding, M05RlsBinding)
        assert binding.tenant_id == TENANT_A
        assert binding.correlation_id == CORRELATION
        assert binding.database_transaction_id == str(pg.txid)
        assert binding.trusted_service is False
        assert binding.all_verified() is True
        assert pg.setting("app.tenant_id") == TENANT_A
        assert pg.setting("app.user_id") == SUBJECT_A
        assert pg.setting("app.role") == "tenant_admin"

    assert not any(statement.upper().startswith("SET ") for statement in pg.statements)


@pytest.mark.asyncio
async def test_m05_binding_is_tenant_wide_because_the_contract_has_no_scope_fields() -> None:
    pg = FakePostgres()
    session = FakeAsyncSession(pg)
    # An earlier M04-style context in the same transaction pins a cabinet.
    async with session.begin():
        pg.gucs["app.cabinet_id"] = "cab_bbbbbbbbbbbbbbbbbbbb"
        pg.gucs["app.project_id"] = "prj_bbbbbbbbbbbbbbbbbbbb"

        binding = await _bind(adapter(), session)

        # ``M05RlsBinding`` cannot carry cabinet/project and the M05 runtime context has
        # none, so the adapter binds tenant-wide instead of inventing a narrower scope.
        assert pg.setting("app.tenant_id") == TENANT_A
        assert pg.setting("app.cabinet_id") == ""
        assert pg.setting("app.project_id") == ""
        assert not hasattr(binding, "cabinet_id")
        assert not hasattr(binding, "project_id")


@pytest.mark.asyncio
async def test_a_non_trusted_bind_clears_a_leftover_trusted_service_flag() -> None:
    pg = FakePostgres()
    session = FakeAsyncSession(pg)

    async with session.begin():
        pg.gucs["app.trusted_service"] = "true"
        binding = await _bind(adapter(), session, trusted=False)
        assert binding.trusted_service is False
        assert pg.setting("app.trusted_service") == ""


@pytest.mark.asyncio
async def test_clear_restores_the_pre_existing_settings() -> None:
    pg = FakePostgres()
    session = FakeAsyncSession(pg)
    port = adapter()

    async with session.begin():
        pg.gucs["app.tenant_id"] = TENANT_B
        pg.gucs["app.cabinet_id"] = "cab_bbbbbbbbbbbbbbbbbbbb"
        pg.gucs["app.role"] = "tenant_viewer"

        await _bind(port, session)
        assert pg.setting("app.tenant_id") == TENANT_A

        await port.clear(session=session)
        assert pg.setting("app.tenant_id") == TENANT_B
        assert pg.setting("app.cabinet_id") == "cab_bbbbbbbbbbbbbbbbbbbb"
        assert pg.setting("app.role") == "tenant_viewer"
        # Never-set settings come back empty, which every reviewed predicate treats as
        # absent (``nullif(current_setting(...), '')``).
        assert pg.setting("app.user_id") in (None, "")

        before = pg.applied_settings()
        await port.clear(session=session)  # idempotent no-op
        assert pg.applied_settings() == before


@pytest.mark.asyncio
async def test_repeated_bind_is_idempotent() -> None:
    pg = FakePostgres()
    session = FakeAsyncSession(pg)
    port = adapter()

    async with session.begin():
        pg.gucs["app.tenant_id"] = TENANT_B
        first = await _bind(port, session)
        second = await _bind(port, session)
        assert first == second
        await port.clear(session=session)
        assert pg.setting("app.tenant_id") == TENANT_B


# ---------------------------------------------------------------------------
# Negative
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_bind_outside_a_transaction_is_refused_without_sql() -> None:
    pg = FakePostgres()
    session = FakeAsyncSession(pg)

    with pytest.raises(RlsContextError) as exc_info:
        await _bind(adapter(), session)
    assert reason(exc_info) == "transaction_required"
    assert pg.statements == []


@pytest.mark.asyncio
async def test_session_factory_is_refused() -> None:
    session = FakeAsyncSession(FakePostgres())
    factory = FakeSessionFactory(session)

    with pytest.raises(RlsContextError) as exc_info:
        await _bind(adapter(), factory)  # type: ignore[arg-type]
    assert reason(exc_info) == "session_factory_not_accepted"
    assert factory.opened is False


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["tenant_id", "subject_id", "session_id"])
async def test_missing_identities_are_refused(field: str) -> None:
    pg = FakePostgres()
    session = FakeAsyncSession(pg)
    async with session.begin(), pytest.raises(RlsContextError) as exc_info:
        await _bind(adapter(), session, **{field: ""})
    assert reason(exc_info) == "tenant_context_missing"
    assert pg.statements == []


@pytest.mark.asyncio
async def test_cross_tenant_identity_is_refused_and_writes_nothing() -> None:
    pg = FakePostgres()
    session = FakeAsyncSession(pg)
    port = adapter(StubResolver(build_principal(tenant_id=TENANT_A)))

    async with session.begin(), pytest.raises(RlsContextError) as exc_info:
        await _bind(port, session, tenant_id=TENANT_B)
    assert reason(exc_info) == "cross_tenant_context"
    assert pg.statements == []
    assert pg.setting("app.tenant_id") is None


@pytest.mark.asyncio
async def test_cross_subject_identity_is_refused() -> None:
    pg = FakePostgres()
    session = FakeAsyncSession(pg)
    port = adapter(StubResolver(build_principal(subject_id=SUBJECT_A)))

    async with session.begin(), pytest.raises(RlsContextError) as exc_info:
        await _bind(port, session, subject_id="emp_bbbbbbbbbbbbbbbbbbbb")
    assert reason(exc_info) == "subject_context_mismatch"
    assert pg.statements == []


@pytest.mark.asyncio
async def test_unresolvable_correlation_is_refused() -> None:
    pg = FakePostgres()
    session = FakeAsyncSession(pg)
    async with session.begin(), pytest.raises(RlsContextError) as exc_info:
        await _bind(adapter(StubResolver(None)), session, correlation_id=CORRELATION_OTHER)
    assert reason(exc_info) == "principal_not_server_resolved"
    assert pg.statements == []


@pytest.mark.asyncio
async def test_trusted_flag_must_be_an_explicit_bool() -> None:
    pg = FakePostgres()
    session = FakeAsyncSession(pg)
    async with session.begin(), pytest.raises(RlsContextError) as exc_info:
        await _bind(adapter(), session, trusted="yes")  # type: ignore[arg-type]
    assert reason(exc_info) == "tenant_context_invalid"
    assert pg.statements == []


@pytest.mark.asyncio
async def test_trusted_capability_requires_the_service_role() -> None:
    pg = FakePostgres(role="prodavan_app")
    session = FakeAsyncSession(pg)

    async with session.begin(), pytest.raises(RlsContextError) as exc_info:
        await _bind(adapter(), session, trusted=True)
    assert reason(exc_info) == "trusted_service_role_mismatch"
    assert pg.statements_with("set_config(") == []


@pytest.mark.asyncio
async def test_trusted_capability_sets_the_flag_with_the_service_role() -> None:
    pg = FakePostgres(role=TRUSTED_SERVICE_ROLE)
    session = FakeAsyncSession(pg)
    principal = build_principal(resolution_source="internal_service_mtls")

    async with session.begin():
        binding = await _bind(adapter(StubResolver(principal)), session, trusted=True)
        assert pg.setting("app.trusted_service") == "true"
        assert binding.trusted_service is True

    # The flag never survives the transaction.
    assert pg.setting("app.trusted_service") is None


@pytest.mark.asyncio
async def test_role_bypass_is_refused() -> None:
    pg = FakePostgres(role="prodavan_platform", bypass_rls=True)
    session = FakeAsyncSession(pg)
    async with session.begin(), pytest.raises(RlsContextError) as exc_info:
        await _bind(adapter(), session)
    assert reason(exc_info) == "database_role_bypasses_rls"
    assert pg.statements_with("set_config(") == []


@pytest.mark.asyncio
async def test_unproven_policies_are_reported_not_assumed() -> None:
    pg = FakePostgres()
    pg.manifest["m05_plugin_instances"] = {"tenant_policy_with_check": None}
    session = FakeAsyncSession(pg)

    async with session.begin():
        binding = await _bind(adapter(), session)
    assert binding.with_check_policy_enforced is False
    assert binding.all_verified() is False


@pytest.mark.asyncio
async def test_adapter_cannot_be_constructed_without_a_resolver() -> None:
    with pytest.raises(ValueError):
        RlsTrustedTenantContextAdapter(principal_resolver=None)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_default_relations_are_the_reviewed_runtime_relations() -> None:
    port = adapter()
    assert port.relations == M05_RUNTIME_RELATIONS
    assert "m05_plugin_instances" in port.relations


def test_scope_contract_gap_is_recorded_not_silently_widened() -> None:
    """The M05 port truly cannot express a cabinet/project scope.

    Documenting it as a contract fact (not a workaround) is the point: when the port gains
    the scope parameter or ``M05RlsBinding`` gains the fields, this test fails and the
    tenant-wide binding must be revisited.
    """

    assert M05_SCOPE_CONTRACT_GAP == "m05_trusted_tenant_context_port_has_no_cabinet_scope"
    bind_parameters = inspect.signature(TrustedTenantContextPort.bind).parameters
    assert "cabinet_id" not in bind_parameters
    assert "project_id" not in bind_parameters
    binding_fields = {field.name for field in dataclasses.fields(M05RlsBinding)}
    assert "cabinet_id" not in binding_fields
    assert "project_id" not in binding_fields
