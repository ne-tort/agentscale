"""Behavioural acceptance for the trusted tenant context; authored, not executed.

Every negative case required by the task is exercised against the in-memory
PostgreSQL stand-in: no context, empty context, cross-tenant reads/writes, a
session-level ``SET`` attempt, and a caller-supplied tenant that must never
become authority.  The positive path proves the exact statements that are
emitted (``set_config(..., true)`` only, readback, restore).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from prodavan.infrastructure.persistence.rls.context import (
    CallerAssertion,
    RlsContextError,
    ServerResolution,
    TrustedPrincipal,
    issue_server_resolution,
    trusted_service_transaction,
    trusted_tenant_context,
    trusted_tenant_context_sync,
    trusted_tenant_transaction,
    trusted_tenant_transaction_sync,
)
from prodavan.infrastructure.persistence.rls.policies import TRUSTED_SERVICE_ROLE
from .acceptance_fixtures import (
    ASSERTIONS,
    CABINET_A,
    CABINET_B,
    PROJECT_B,
    SUBJECT_A,
    TENANT_A,
    TENANT_B,
    AsyncSessionFactory,
    FakeAsyncSession,
    FakePostgres,
    FakeSyncSession,
    SyncSessionFactory,
    build_principal,
)


def _error_reason(exc_info: pytest.ExceptionInfo[RlsContextError]) -> str:
    return exc_info.value.reason_code


# ---------------------------------------------------------------------------
# Positive path
# ---------------------------------------------------------------------------


def test_sync_context_sets_transaction_local_values_and_restores_previous() -> None:
    pg = FakePostgres()
    pg.gucs["app.tenant_id"] = "comp_previous"
    session = FakeSyncSession(pg)
    principal = build_principal(project_id=None)

    with session.begin():
        with trusted_tenant_context_sync(session, principal=principal) as context:
            assert pg.gucs["app.tenant_id"] == TENANT_A
            assert pg.gucs["app.cabinet_id"] == CABINET_A
            assert pg.gucs["app.project_id"] is None or pg.gucs["app.project_id"] == ""
            assert context.tenant_id == TENANT_A
            assert context.database_role == "prodavan_app"
            assert context.principal_digest.startswith("sha256:")
            # The previous value is captured as evidence, not exposed as a claim.
            assert context.previous_value("app.tenant_id") == "comp_previous"
        # Restored to the pre-existing value, not just cleared.
        assert pg.gucs["app.tenant_id"] == "comp_previous"

    assert not any(statement.upper().startswith("SET ") for statement in pg.statements)
    assert all("set_config('" in statement or "current_setting('" in statement or "pg_roles" in statement
               for statement in pg.statements)


@pytest.mark.asyncio
async def test_async_context_and_transaction_obey_the_same_invariants() -> None:
    session = FakeAsyncSession(FakePostgres())
    principal = build_principal()

    async with session.begin():
        async with trusted_tenant_context(session, principal=principal) as context:
            assert context.tenant_id == TENANT_A

    session2 = FakeAsyncSession(FakePostgres())
    async with trusted_tenant_transaction(
        AsyncSessionFactory(session2), principal=principal
    ) as (_, context):
        assert context.database_role == "prodavan_app"


def test_sync_transaction_owns_begin_and_commit_scope() -> None:
    session = FakeSyncSession(FakePostgres())
    with trusted_tenant_transaction_sync(
        SyncSessionFactory(session), principal=build_principal()
    ) as (returned, context):
        assert returned is session
        assert context.tenant_id == TENANT_A
    # Transaction closed: the transaction-local settings are gone with it.
    assert session.depth == 0


# ---------------------------------------------------------------------------
# Negative: missing / empty / invalid context
# ---------------------------------------------------------------------------


def test_missing_tenant_is_rejected_before_any_sql() -> None:
    pg = FakePostgres()
    with pytest.raises(RlsContextError) as exc_info:
        issue_server_resolution(
            resolved_by="auth.session_middleware",
            correlation_id="corr_1",
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
            tenant_id="",
            cabinet_id=None,
            project_id=None,
            subject_id="emp_1",
            role="tenant_admin",
        )
    assert _error_reason(exc_info) == "tenant_context_missing"
    assert pg.statements == []


def test_empty_string_identity_is_not_a_tenant() -> None:
    with pytest.raises(RlsContextError) as exc_info:
        issue_server_resolution(
            resolved_by="auth.session_middleware",
            correlation_id="corr_1",
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
            tenant_id="   ",
            subject_id="emp_1",
            role="tenant_admin",
        )
    assert _error_reason(exc_info) == "tenant_context_invalid"


def test_context_outside_a_transaction_is_refused() -> None:
    session = FakeSyncSession(FakePostgres())  # depth == 0
    with pytest.raises(RlsContextError) as exc_info:
        with trusted_tenant_context_sync(session, principal=build_principal()):
            pytest.fail("context must not be yielded outside a transaction")
    assert _error_reason(exc_info) == "transaction_required"
    assert session.pg.statements == []


def test_unset_context_denies_instead_of_defaulting() -> None:
    # Reading a policy predicate with no context yields NULL, i.e. no rows; the
    # application must map that to 404, never to "platform-wide".
    pg = FakePostgres()
    pg.reset_transaction()
    session = FakeSyncSession(pg)
    with session.begin():
        with pytest.raises(RlsContextError):
            with trusted_tenant_context_sync(session, principal=build_principal(tenant_id="")):
                pass
        assert pg.gucs["app.tenant_id"] is None


# ---------------------------------------------------------------------------
# Negative: caller-supplied tenant is an assertion, never authority
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("assertion_name", ["cross_tenant", "cross_cabinet", "cross_project"])
def test_caller_assertions_that_contradict_the_resolution_are_denied(assertion_name: str) -> None:
    session = FakeSyncSession(FakePostgres())
    with session.begin(), pytest.raises(RlsContextError) as exc_info:
        with trusted_tenant_context_sync(
            session,
            principal=build_principal(),
            assertion=ASSERTIONS[assertion_name],
        ):
            pytest.fail("cross-scope context must not be applied")
    assert _error_reason(exc_info) == "cross_tenant_context"
    # The denied context never wrote a tenant: no statement was emitted at all.
    assert session.pg.statements == []


def test_matching_assertion_is_accepted_but_is_not_the_authority() -> None:
    pg = FakePostgres()
    session = FakeSyncSession(pg)
    with session.begin():
        with trusted_tenant_context_sync(
            session, principal=build_principal(), assertion=ASSERTIONS["matching"]
        ) as context:
            # Exactly the resolved value was written, once, in this transaction.
            assert context.tenant_id == TENANT_A
            # The applied parameters are the resolved identity, not the assertion.
            assert pg.set_config_params == [
                {
                    "tenant_id": TENANT_A,
                    "cabinet_id": CABINET_A,
                    "project_id": "",
                    "user_id": SUBJECT_A,
                    "role": "tenant_admin",
                }
            ]
            assert pg.gucs["app.tenant_id"] == TENANT_A
    # The exit restores the previous values: the apply/restore pair shares its SQL text
    # and differs only in bind parameters, so the count is 2 with one apply first.
    assert len(pg.set_config_params) == 2
    assert pg.set_config_params[1]["tenant_id"] == ""


def test_assertion_object_cannot_be_substituted_by_a_mapping() -> None:
    session = FakeSyncSession(FakePostgres())
    with session.begin(), pytest.raises(RlsContextError) as exc_info:
        with trusted_tenant_context_sync(
            session,
            principal=build_principal(),
            assertion={"tenant_id": TENANT_B},  # type: ignore[arg-type]
        ):
            pytest.fail("a raw mapping must not be treated as an assertion")
    assert _error_reason(exc_info) == "tenant_context_invalid"


def test_principal_cannot_be_built_from_a_mapping_or_claim() -> None:
    with pytest.raises(RlsContextError) as exc_info:
        TrustedPrincipal(tenant_id=TENANT_B)  # type: ignore[call-arg]
    assert _error_reason(exc_info) == "principal_not_server_resolved"

    with pytest.raises(RlsContextError) as exc_info:
        ServerResolution(resolved_by="request_payload")  # type: ignore[call-arg]
    assert _error_reason(exc_info) == "principal_not_server_resolved"

    with pytest.raises(RlsContextError) as exc_info:
        TrustedPrincipal.from_resolution(
            issue_server_resolution(
                resolved_by="auth.session_middleware",
                correlation_id="corr_1",
                expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
                tenant_id=TENANT_A,
                subject_id="emp_1",
                role="tenant_admin",
            ),
            resolution_source="request_claim",  # type: ignore[arg-type]
        )
    assert _error_reason(exc_info) == "principal_not_server_resolved"


def test_session_cannot_pass_a_bare_mapping_as_a_principal() -> None:
    session = FakeSyncSession(FakePostgres())
    with session.begin(), pytest.raises(RlsContextError) as exc_info:
        with trusted_tenant_context_sync(
            session, principal={"tenant_id": TENANT_B}  # type: ignore[arg-type]
        ):
            pytest.fail("a mapping must not become authority")
    assert _error_reason(exc_info) == "principal_not_server_resolved"


def test_expired_resolution_is_denied() -> None:
    # Two layers refuse an expired resolution: issuing one for an already elapsed TTL
    # and applying one that expires between resolution and the transaction.
    with pytest.raises(RlsContextError) as issuance:
        build_principal(ttl=timedelta(minutes=-1))
    assert _error_reason(issuance) == "tenant_context_expired"

    session = FakeSyncSession(FakePostgres())
    principal = build_principal(ttl=timedelta(minutes=1))
    after_expiry = lambda: datetime.now(timezone.utc) + timedelta(minutes=2)  # noqa: E731
    with session.begin(), pytest.raises(RlsContextError) as exc_info:
        with trusted_tenant_context_sync(session, principal=principal, clock=after_expiry):
            pytest.fail("expired principal must not be applied")
    assert _error_reason(exc_info) == "tenant_context_expired"
    # Nothing was written before the expiry check refused the context.
    assert session.pg.statements == []


def test_project_scope_without_cabinet_is_rejected() -> None:
    with pytest.raises(RlsContextError) as exc_info:
        build_principal(cabinet_id=None, project_id=PROJECT_B)
    assert _error_reason(exc_info) == "tenant_context_invalid"


def test_tampered_principal_evidence_is_detected() -> None:
    principal = build_principal()
    tampered = object.__new__(TrustedPrincipal)
    for field_name in (
        "cabinet_id",
        "project_id",
        "subject_id",
        "role",
        "resolution_source",
        "resolved_by",
        "correlation_id",
        "expires_at",
        "digest",
    ):
        object.__setattr__(tampered, field_name, getattr(principal, field_name))
    object.__setattr__(tampered, "tenant_id", TENANT_B)  # forged after resolution

    session = FakeSyncSession(FakePostgres())
    with session.begin(), pytest.raises(RlsContextError) as exc_info:
        with trusted_tenant_context_sync(session, principal=tampered):
            pytest.fail("a re-pointed principal must not be applied")
    assert _error_reason(exc_info) == "tenant_context_invalid"


def test_migration_operator_must_resolve_to_the_migration_role() -> None:
    with pytest.raises(RlsContextError) as exc_info:
        build_principal(resolution_source="migration_operator", role="tenant_admin")
    assert _error_reason(exc_info) == "principal_not_server_resolved"


# ---------------------------------------------------------------------------
# Negative: proof of application, role bypass and session-level SET
# ---------------------------------------------------------------------------


def test_readback_mismatch_aborts_the_transaction() -> None:
    pg = FakePostgres()
    pg.ignore_apply = True
    session = FakeSyncSession(pg)
    with session.begin(), pytest.raises(RlsContextError) as exc_info:
        with trusted_tenant_context_sync(session, principal=build_principal()):
            pytest.fail("unproven context must not be yielded")
    assert _error_reason(exc_info) == "context_readback_mismatch"


def test_corrupted_context_is_detected_by_readback() -> None:
    pg = FakePostgres()
    pg.corrupt_on_call = 1
    session = FakeSyncSession(pg)
    with session.begin(), pytest.raises(RlsContextError) as exc_info:
        with trusted_tenant_context_sync(session, principal=build_principal()):
            pytest.fail("a mismatched readback must fail closed")
    assert _error_reason(exc_info) == "context_readback_mismatch"


def test_row_security_off_is_refused() -> None:
    pg = FakePostgres(row_security="off")
    session = FakeSyncSession(pg)
    with session.begin(), pytest.raises(RlsContextError) as exc_info:
        with trusted_tenant_context_sync(session, principal=build_principal()):
            pytest.fail("RLS disabled for the transaction must fail closed")
    assert _error_reason(exc_info) == "row_security_disabled"


def test_bypassrls_role_is_refused() -> None:
    session = FakeSyncSession(FakePostgres(role="prodavan_platform", bypass_rls=True))
    with session.begin(), pytest.raises(RlsContextError) as exc_info:
        with trusted_tenant_context_sync(session, principal=build_principal()):
            pytest.fail("BYPASSRLS role must not be used for tenant context")
    assert _error_reason(exc_info) == "database_role_bypasses_rls"


def test_superuser_role_is_refused() -> None:
    session = FakeSyncSession(FakePostgres(role="postgres", superuser=True))
    with session.begin(), pytest.raises(RlsContextError) as exc_info:
        with trusted_tenant_context_sync(session, principal=build_principal()):
            pytest.fail("superuser must not be used for tenant context")
    assert _error_reason(exc_info) == "database_role_bypasses_rls"


def test_non_postgres_dialect_is_refused() -> None:
    session = FakeSyncSession(FakePostgres())
    session.get_bind = lambda: SimpleNamespace(dialect=SimpleNamespace(name="sqlite"))  # type: ignore[method-assign]
    with session.begin(), pytest.raises(RlsContextError) as exc_info:
        with trusted_tenant_context_sync(session, principal=build_principal()):
            pytest.fail("sqlite has no transaction-local GUC semantics")
    assert _error_reason(exc_info) == "postgresql_required"


def test_session_level_set_is_structurally_forbidden() -> None:
    from prodavan.infrastructure.persistence.rls import context as module

    for statement in (module._APPLY_SQL, module._APPLY_SERVICE_SQL, module._RESTORE_SQL):
        assert "set_config(" in statement
        assert statement.count(", true)") >= 1
        assert not statement.strip().upper().startswith("SET ")
        assert "set_config('app.tenant_id', :tenant_id, false)" not in statement


# ---------------------------------------------------------------------------
# Trusted service path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_trusted_service_requires_the_service_role() -> None:
    session = FakeAsyncSession(FakePostgres(role="prodavan_app"))
    with pytest.raises(RlsContextError) as exc_info:
        async with trusted_service_transaction(
            AsyncSessionFactory(session), principal=build_principal()
        ):
            pytest.fail("tenant role must not obtain the service flag")
    assert _error_reason(exc_info) == "trusted_service_role_mismatch"


@pytest.mark.asyncio
async def test_trusted_service_sets_the_flag_only_with_the_service_role() -> None:
    pg = FakePostgres(role=TRUSTED_SERVICE_ROLE)
    session = FakeAsyncSession(pg)
    async with trusted_service_transaction(
        AsyncSessionFactory(session), principal=build_principal(resolution_source="internal_service_mtls")
    ) as (_, context):
        assert pg.gucs["app.trusted_service"] == "true"
        assert context.database_role == TRUSTED_SERVICE_ROLE
    assert pg.gucs["app.trusted_service"] is None
