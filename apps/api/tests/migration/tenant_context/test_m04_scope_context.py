"""Acceptance for the M04 ``TenantScopeContextPort`` implementation; authored, not run.

Each test states one acceptance obligation of the task:

* bind inside a transaction writes the GUCs transaction-locally and the previous values
  can be restored (``clear``);
* bind outside a transaction is refused before any SQL;
* a missing or empty tenant is refused;
* a cross-tenant call is refused and writes nothing;
* a repeated bind is idempotent (same evidence, one captured "previous", no drift);
* ``clear`` returns the connection to the state that preceded the first bind;
* no ``SET`` without ``LOCAL`` is ever emitted (also proven statically in
  ``test_static_and_wiring_contract.py``).
"""

from __future__ import annotations

import pytest

from prodavan.application.migration.m04_scope.scope_persistence import (
    RlsBindingEvidence,
    TenantScopeMode,
)
from prodavan.application.migration.tenant_context import (
    M04_SCOPE_RELATIONS,
    SECURITY_REVIEW_ID,
    RlsTenantScopeContextAdapter,
)
from prodavan.infrastructure.persistence.rls.context import RlsContextError
from prodavan.infrastructure.persistence.rls.policies import TRUSTED_SERVICE_ROLE

from tests.migration.tenant_context.postgres_double import (
    APPLICATION_TRANSACTION,
    CABINET_A,
    CABINET_B,
    CORRELATION_OTHER,
    PROJECT_A,
    SUBJECT_A,
    TENANT_A,
    TENANT_B,
    FakeAsyncSession,
    FakePostgres,
    FakeSessionFactory,
    StubResolver,
    build_principal,
    evidence_context,
    expired_principal,
    unusable_context,
)


def adapter(resolver: StubResolver | None = None) -> RlsTenantScopeContextAdapter:
    return RlsTenantScopeContextAdapter(
        principal_resolver=resolver if resolver is not None else StubResolver(build_principal())
    )


def reason(exc_info: pytest.ExceptionInfo[RlsContextError]) -> str:
    return exc_info.value.reason_code


# ---------------------------------------------------------------------------
# Positive path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_bind_inside_transaction_applies_gucs_and_clears_back_to_previous() -> None:
    pg = FakePostgres()
    session = FakeAsyncSession(pg)
    port = adapter()

    async with session.begin():
        # An outer/earlier context in the same transaction: bind must not lose it.
        pg.gucs["app.tenant_id"] = TENANT_B
        pg.gucs["app.cabinet_id"] = CABINET_B
        pg.gucs["app.role"] = "tenant_viewer"

        binding = await port.bind(
            session=session,
            context=evidence_context(),
            mode=TenantScopeMode.TENANT_SCOPED,
        )

        assert pg.setting("app.tenant_id") == TENANT_A
        assert pg.setting("app.cabinet_id") == CABINET_A
        assert pg.setting("app.project_id") == ""
        assert pg.setting("app.user_id") == SUBJECT_A
        assert pg.setting("app.role") == "tenant_admin"
        assert pg.setting("app.trusted_service") == ""

        assert isinstance(binding, RlsBindingEvidence)
        assert binding.tenant_id == TENANT_A
        assert binding.cabinet_id == CABINET_A
        assert binding.project_id is None
        assert binding.application_transaction_id == APPLICATION_TRANSACTION
        assert binding.database_transaction_id == str(pg.txid)
        assert binding.database_role == "prodavan_app"
        assert binding.security_review_id == SECURITY_REVIEW_ID
        assert binding.trusted_service is False
        assert binding.all_verified() is True
        assert set(binding.policy_names) == {
            f"{relation}_tenant_isolation" for relation in M04_SCOPE_RELATIONS
        } | {f"{relation}_trusted_service_access" for relation in M04_SCOPE_RELATIONS}

        await port.clear(session=session)
        assert pg.setting("app.tenant_id") == TENANT_B
        assert pg.setting("app.cabinet_id") == CABINET_B
        assert pg.setting("app.role") == "tenant_viewer"
        # A setting that was never set is restored to the empty value, which every
        # reviewed predicate reads as absent (``nullif(current_setting(...), '')``).
        assert pg.setting("app.user_id") in (None, "")

    assert not any(statement.upper().startswith("SET ") for statement in pg.statements)
    assert all(
        "set_config(" in statement or "current_setting(" in statement
        or "pg_roles" in statement or "pg_policies" in statement or "txid_current" in statement
        for statement in pg.statements
    )


@pytest.mark.asyncio
async def test_every_write_statement_is_transaction_local() -> None:
    pg = FakePostgres()
    session = FakeAsyncSession(pg)
    port = adapter()

    async with session.begin():
        await port.bind(
            session=session,
            context=evidence_context(),
            mode=TenantScopeMode.TENANT_SCOPED,
        )
        await port.clear(session=session)

    writes = [statement for statement in pg.statements if "set_config(" in statement]
    assert writes, "the binder must actually write the settings"
    for statement in writes:
        assert statement.count("set_config(") == statement.count(", true)")


@pytest.mark.asyncio
async def test_tenant_scoped_bind_clears_a_leftover_trusted_service_flag() -> None:
    pg = FakePostgres()
    session = FakeAsyncSession(pg)

    async with session.begin():
        # An earlier trusted step in the same transaction set the cross-tenant flag.
        pg.gucs["app.trusted_service"] = "true"

        binding = await adapter().bind(
            session=session,
            context=evidence_context(),
            mode=TenantScopeMode.TENANT_SCOPED,
        )
        assert binding.trusted_service is False
        # A tenant-scoped step must not inherit the capability it never requested.
        assert pg.setting("app.trusted_service") == ""


@pytest.mark.asyncio
async def test_repeated_bind_is_idempotent_and_keeps_the_first_previous_state() -> None:
    pg = FakePostgres()
    session = FakeAsyncSession(pg)
    port = adapter()

    async with session.begin():
        pg.gucs["app.tenant_id"] = TENANT_B
        first = await port.bind(
            session=session,
            context=evidence_context(),
            mode=TenantScopeMode.TENANT_SCOPED,
        )
        second = await port.bind(
            session=session,
            context=evidence_context(),
            mode=TenantScopeMode.TENANT_SCOPED,
        )

        assert first == second
        assert pg.setting("app.tenant_id") == TENANT_A
        assert first.database_transaction_id == second.database_transaction_id

        await port.clear(session=session)
        # The state captured before the *first* bind is restored, not the second one.
        assert pg.setting("app.tenant_id") == TENANT_B


@pytest.mark.asyncio
async def test_clear_is_idempotent_and_never_touches_a_finished_transaction() -> None:
    pg = FakePostgres()
    session = FakeAsyncSession(pg)
    port = adapter()

    async with session.begin():
        await port.bind(
            session=session,
            context=evidence_context(),
            mode=TenantScopeMode.TENANT_SCOPED,
        )
        await port.clear(session=session)
        before = pg.applied_settings()
        await port.clear(session=session)  # second clear: no-op
        assert pg.applied_settings() == before

    # Once the transaction is gone the settings died with it: ``clear`` is a silent
    # no-op that emits no SQL instead of raising or re-opening anything.
    pg.statements.clear()
    await port.clear(session=session)
    assert pg.statements == []


# ---------------------------------------------------------------------------
# Negative: transaction locality
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_bind_outside_a_transaction_is_refused_without_sql() -> None:
    pg = FakePostgres()
    session = FakeAsyncSession(pg)  # depth == 0

    with pytest.raises(RlsContextError) as exc_info:
        await adapter().bind(
            session=session,
            context=evidence_context(),
            mode=TenantScopeMode.TENANT_SCOPED,
        )
    assert reason(exc_info) == "transaction_required"
    assert pg.statements == []
    assert all(value is None for value in pg.applied_settings().values())


@pytest.mark.asyncio
async def test_non_postgres_dialect_is_refused() -> None:
    session = FakeAsyncSession(FakePostgres(), dialect="sqlite")
    async with session.begin(), pytest.raises(RlsContextError) as exc_info:
        await adapter().bind(
            session=session,
            context=evidence_context(),
            mode=TenantScopeMode.TENANT_SCOPED,
        )
    assert reason(exc_info) == "postgresql_required"
    assert session.pg.statements == []


@pytest.mark.asyncio
async def test_session_factory_is_refused_and_never_opened() -> None:
    pg = FakePostgres()
    session = FakeAsyncSession(pg)
    factory = FakeSessionFactory(session)

    with pytest.raises(RlsContextError) as exc_info:
        await adapter().bind(  # type: ignore[arg-type]
            session=factory,  # a factory, not an open session
            context=evidence_context(),
            mode=TenantScopeMode.TENANT_SCOPED,
        )
    assert reason(exc_info) == "session_factory_not_accepted"
    assert factory.opened is False
    assert pg.statements == []


# ---------------------------------------------------------------------------
# Negative: missing / empty / unusable context
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_missing_tenant_is_refused_before_any_sql() -> None:
    pg = FakePostgres()
    session = FakeAsyncSession(pg)
    async with session.begin(), pytest.raises(RlsContextError) as exc_info:
        await adapter().bind(
            session=session,
            context=unusable_context(tenant_id=""),
            mode=TenantScopeMode.TENANT_SCOPED,
        )
    assert reason(exc_info) == "trusted_transaction_context_missing"
    assert pg.statements == []
    assert pg.setting("app.tenant_id") is None


@pytest.mark.asyncio
async def test_unusable_context_without_resolved_rls_context_is_refused() -> None:
    pg = FakePostgres()
    session = FakeAsyncSession(pg)
    async with session.begin(), pytest.raises(RlsContextError) as exc_info:
        await adapter().bind(
            session=session,
            context=unusable_context(),
            mode=TenantScopeMode.TENANT_SCOPED,
        )
    assert reason(exc_info) == "trusted_transaction_context_missing"
    assert pg.statements == []


@pytest.mark.asyncio
async def test_missing_resolution_leaves_no_context_behind() -> None:
    pg = FakePostgres()
    session = FakeAsyncSession(pg)
    port = adapter(StubResolver(None))
    async with session.begin(), pytest.raises(RlsContextError) as exc_info:
        await port.bind(
            session=session,
            context=evidence_context(),
            mode=TenantScopeMode.TENANT_SCOPED,
        )
    assert reason(exc_info) == "principal_not_server_resolved"
    assert pg.setting("app.tenant_id") is None


# ---------------------------------------------------------------------------
# Negative: caller-supplied scope is an assertion, never authority
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cross_tenant_assertion_is_denied_and_writes_nothing() -> None:
    pg = FakePostgres()
    session = FakeAsyncSession(pg)
    # The transaction claims tenant B; the server resolved tenant A for this correlation.
    port = adapter(StubResolver(build_principal(tenant_id=TENANT_A)))

    async with session.begin(), pytest.raises(RlsContextError) as exc_info:
        await port.bind(
            session=session,
            context=evidence_context(tenant_id=TENANT_B, cabinet_id=CABINET_B),
            mode=TenantScopeMode.TENANT_SCOPED,
        )
    assert reason(exc_info) == "cross_tenant_context"
    assert pg.statements == []


@pytest.mark.asyncio
async def test_cross_cabinet_assertion_is_denied() -> None:
    pg = FakePostgres()
    session = FakeAsyncSession(pg)
    port = adapter(StubResolver(build_principal(cabinet_id=CABINET_A)))

    async with session.begin(), pytest.raises(RlsContextError) as exc_info:
        await port.bind(
            session=session,
            context=evidence_context(cabinet_id=CABINET_B),
            mode=TenantScopeMode.TENANT_SCOPED,
        )
    assert reason(exc_info) == "cross_tenant_context"
    assert pg.statements == []


@pytest.mark.asyncio
async def test_subject_and_role_assertions_must_match_the_principal() -> None:
    pg = FakePostgres()
    session = FakeAsyncSession(pg)
    port = adapter(StubResolver(build_principal(subject_id=SUBJECT_A)))

    async with session.begin(), pytest.raises(RlsContextError) as exc_info:
        await port.bind(
            session=session,
            context=evidence_context(subject_id="emp_bbbbbbbbbbbbbbbbbbbb"),
            mode=TenantScopeMode.TENANT_SCOPED,
        )
    assert reason(exc_info) == "subject_context_mismatch"

    async with session.begin(), pytest.raises(RlsContextError) as exc_info:
        await port.bind(
            session=session,
            context=evidence_context(role="platform_admin"),
            mode=TenantScopeMode.TENANT_SCOPED,
        )
    assert reason(exc_info) == "subject_context_mismatch"
    assert pg.statements == []


@pytest.mark.asyncio
async def test_resolver_must_return_the_sealed_principal() -> None:
    pg = FakePostgres()
    session = FakeAsyncSession(pg)
    port = adapter(StubResolver({"tenant_id": TENANT_A}))

    async with session.begin(), pytest.raises(RlsContextError) as exc_info:
        await port.bind(
            session=session,
            context=evidence_context(),
            mode=TenantScopeMode.TENANT_SCOPED,
        )
    assert reason(exc_info) == "principal_not_server_resolved"
    assert pg.statements == []


@pytest.mark.asyncio
async def test_principal_for_another_correlation_is_refused() -> None:
    pg = FakePostgres()
    session = FakeAsyncSession(pg)
    port = adapter(StubResolver(build_principal(correlation_id=CORRELATION_OTHER)))

    async with session.begin(), pytest.raises(RlsContextError) as exc_info:
        await port.bind(
            session=session,
            context=evidence_context(),
            mode=TenantScopeMode.TENANT_SCOPED,
        )
    assert reason(exc_info) == "principal_not_server_resolved"
    assert pg.statements == []


@pytest.mark.asyncio
async def test_expired_principal_is_refused_every_time() -> None:
    pg = FakePostgres()
    session = FakeAsyncSession(pg)
    port = adapter(StubResolver(expired_principal()))

    async with session.begin(), pytest.raises(RlsContextError) as exc_info:
        await port.bind(
            session=session,
            context=evidence_context(),
            mode=TenantScopeMode.TENANT_SCOPED,
        )
    assert reason(exc_info) == "tenant_context_expired"
    assert pg.statements == []


@pytest.mark.asyncio
async def test_adapter_cannot_be_constructed_without_a_resolver() -> None:
    with pytest.raises(ValueError):
        RlsTenantScopeContextAdapter(principal_resolver=None)  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        RlsTenantScopeContextAdapter(principal_resolver=object())  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_unknown_relation_cannot_be_reviewed_or_proved() -> None:
    with pytest.raises(RlsContextError) as exc_info:
        RlsTenantScopeContextAdapter(
            principal_resolver=StubResolver(build_principal()),
            relations=("scope_registry", "public_not_reviewed_table"),
        )
    assert reason(exc_info) == "rls_policy_not_reviewed"

    with pytest.raises(RlsContextError) as exc_info:
        RlsTenantScopeContextAdapter(
            principal_resolver=StubResolver(build_principal()), relations=()
        )
    assert reason(exc_info) == "rls_relations_required"


# ---------------------------------------------------------------------------
# Negative: unproven application, role bypass, policy gaps
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_readback_mismatch_aborts_before_yielding_evidence() -> None:
    pg = FakePostgres()
    pg.ignore_apply = True
    session = FakeAsyncSession(pg)
    async with session.begin(), pytest.raises(RlsContextError) as exc_info:
        await adapter().bind(
            session=session,
            context=evidence_context(),
            mode=TenantScopeMode.TENANT_SCOPED,
        )
    assert reason(exc_info) == "context_readback_mismatch"


@pytest.mark.asyncio
async def test_corrupted_application_is_detected_by_readback() -> None:
    pg = FakePostgres()
    pg.corrupt_on_call = 1
    session = FakeAsyncSession(pg)
    async with session.begin(), pytest.raises(RlsContextError) as exc_info:
        await adapter().bind(
            session=session,
            context=evidence_context(),
            mode=TenantScopeMode.TENANT_SCOPED,
        )
    assert reason(exc_info) == "context_readback_mismatch"


@pytest.mark.asyncio
async def test_bypassrls_and_superuser_roles_are_refused() -> None:
    for kwargs in ({"role": "prodavan_platform", "bypass_rls": True}, {"role": "postgres", "superuser": True}):
        pg = FakePostgres(**kwargs)
        session = FakeAsyncSession(pg)
        async with session.begin(), pytest.raises(RlsContextError) as exc_info:
            await adapter().bind(
                session=session,
                context=evidence_context(),
                mode=TenantScopeMode.TENANT_SCOPED,
            )
        assert reason(exc_info) == "database_role_bypasses_rls"
        assert pg.statements_with("set_config(") == []


@pytest.mark.asyncio
async def test_row_security_off_is_refused() -> None:
    pg = FakePostgres(row_security="off")
    session = FakeAsyncSession(pg)
    async with session.begin(), pytest.raises(RlsContextError) as exc_info:
        await adapter().bind(
            session=session,
            context=evidence_context(),
            mode=TenantScopeMode.TENANT_SCOPED,
        )
    assert reason(exc_info) == "row_security_disabled"


@pytest.mark.asyncio
async def test_missing_relation_makes_every_policy_fact_false() -> None:
    pg = FakePostgres()
    pg.missing_relations.add("scope_owner_audit")
    session = FakeAsyncSession(pg)

    async with session.begin():
        binding = await adapter().bind(
            session=session,
            context=evidence_context(),
            mode=TenantScopeMode.TENANT_SCOPED,
        )

    # The binder reports what it measured; M04 refuses the step on ``all_verified()``.
    assert binding.all_verified() is False
    assert binding.row_security_enabled is False
    assert binding.row_security_forced is False
    assert binding.tenant_policy_enforced is False
    assert "scope_owner_audit_tenant_isolation" not in binding.policy_names


@pytest.mark.asyncio
async def test_a_restrictive_or_commandless_policy_is_not_counted_as_enforced() -> None:
    pg = FakePostgres()
    pg.manifest["scope_owner_mappings"] = {
        "tenant_policy_permissive": "RESTRICTIVE",
        "tenant_policy_command": "SELECT",
    }
    session = FakeAsyncSession(pg)

    async with session.begin():
        binding = await adapter().bind(
            session=session,
            context=evidence_context(),
            mode=TenantScopeMode.TENANT_SCOPED,
        )
    assert binding.tenant_policy_enforced is False
    assert binding.using_policy_enforced is False


@pytest.mark.asyncio
async def test_a_predicate_without_nullif_does_not_deny_the_missing_context() -> None:
    pg = FakePostgres()
    # A predicate that reads the GUC without ``nullif(..., '')`` would let an empty
    # setting compare equal to an empty column instead of denying.
    pg.manifest["scope_registry"] = {
        "tenant_policy_using": "scope_registry.tenant_id = current_setting('app.tenant_id', true)",
        "tenant_policy_with_check": "scope_registry.tenant_id = current_setting('app.tenant_id', true)",
    }
    session = FakeAsyncSession(pg)

    async with session.begin():
        binding = await adapter().bind(
            session=session,
            context=evidence_context(),
            mode=TenantScopeMode.TENANT_SCOPED,
        )
    assert binding.missing_context_denies is False
    assert binding.all_verified() is False


@pytest.mark.asyncio
async def test_deparsed_catalog_predicate_is_normalized() -> None:
    # ``pg_policies.qual`` is the deparsed expression, not the DDL text: casts and
    # quoting differ.  The manifest check must still recognise the reviewed predicate.
    pg = FakePostgres()
    pg.manifest["scope_registry"] = {
        "tenant_policy_using": (
            '(scope_registry.tenant_id = nullif(current_setting(\'app.tenant_id\'::text, true), \'\'::text)) '
            "AND (scope_registry.cabinet_id IS NULL OR scope_registry.cabinet_id = "
            "nullif(current_setting('app.cabinet_id'::text, true), ''::text)) "
            "AND (scope_registry.project_id IS NULL OR scope_registry.project_id = "
            "nullif(current_setting('app.project_id'::text, true), ''::text)) "
            "AND scope_registry.owner_state = 'active'::text"
        ),
    }
    session = FakeAsyncSession(pg)

    async with session.begin():
        binding = await adapter().bind(
            session=session,
            context=evidence_context(),
            mode=TenantScopeMode.TENANT_SCOPED,
        )
    assert binding.tenant_policy_enforced is True
    assert binding.missing_context_denies is True


# ---------------------------------------------------------------------------
# Trusted service mode
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_trusted_mode_requires_the_service_role() -> None:
    pg = FakePostgres(role="prodavan_app")
    session = FakeAsyncSession(pg)

    async with session.begin(), pytest.raises(RlsContextError) as exc_info:
        await adapter().bind(
            session=session,
            context=evidence_context(),
            mode=TenantScopeMode.MIGRATION_TRUSTED,
        )
    assert reason(exc_info) == "trusted_service_role_mismatch"
    assert pg.statements_with("set_config(") == []


@pytest.mark.asyncio
async def test_trusted_mode_sets_the_flag_and_reports_it() -> None:
    pg = FakePostgres(role=TRUSTED_SERVICE_ROLE)
    session = FakeAsyncSession(pg)
    principal = build_principal(resolution_source="migration_operator", role="migration_role")
    port = adapter(StubResolver(principal))

    async with session.begin():
        binding = await port.bind(
            session=session,
            context=evidence_context(role="migration_role"),
            mode=TenantScopeMode.MIGRATION_TRUSTED,
        )
        assert pg.setting("app.trusted_service") == "true"
        assert binding.trusted_service is True
        assert binding.database_role == TRUSTED_SERVICE_ROLE

        await port.clear(session=session)
        assert pg.setting("app.trusted_service") in (None, "")

    assert not any(statement.upper().startswith("SET ") for statement in pg.statements)


@pytest.mark.asyncio
async def test_trusted_mode_requires_the_reviewed_trusted_policy() -> None:
    pg = FakePostgres(role=TRUSTED_SERVICE_ROLE)
    pg.manifest["m04e_dynamic_app_versions"] = {"trusted_policy_name": None}
    session = FakeAsyncSession(pg)
    principal = build_principal(resolution_source="migration_operator", role="migration_role")

    async with session.begin(), pytest.raises(RlsContextError) as exc_info:
        await RlsTenantScopeContextAdapter(
            principal_resolver=StubResolver(principal),
            relations=("m04e_dynamic_app_versions",),
        ).bind(
            session=session,
            context=evidence_context(role="migration_role"),
            mode=TenantScopeMode.MIGRATION_TRUSTED,
        )
    assert reason(exc_info) == "trusted_service_policy_missing"


# ---------------------------------------------------------------------------
# narrow
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_narrow_pins_scope_and_restores_it_on_exit() -> None:
    pg = FakePostgres()
    session = FakeAsyncSession(pg)
    # A tenant-wide resolution: the binding carries no cabinet, so a cabinet may be pinned.
    port = adapter(StubResolver(build_principal(cabinet_id=None)))

    async with session.begin():
        binding = await port.bind(
            session=session,
            context=evidence_context(cabinet_id=None),
            mode=TenantScopeMode.TENANT_SCOPED,
        )
        assert binding.cabinet_id is None
        assert binding.project_id is None
        async with port.narrow(
            session=session, binding=binding, cabinet_id=CABINET_A, project_id=PROJECT_A
        ) as narrowed:
            assert pg.setting("app.cabinet_id") == CABINET_A
            assert pg.setting("app.project_id") == PROJECT_A
            assert narrowed.tenant_id == binding.tenant_id
            assert narrowed.cabinet_id == CABINET_A
            assert narrowed.project_id == PROJECT_A
            assert narrowed.database_transaction_id == binding.database_transaction_id
        # Restored to the binding's (tenant-wide) scope, not left pinned.
        assert pg.setting("app.cabinet_id") == ""
        assert pg.setting("app.project_id") == ""
        assert pg.setting("app.tenant_id") == TENANT_A


@pytest.mark.asyncio
async def test_narrow_widening_is_refused() -> None:
    pg = FakePostgres()
    session = FakeAsyncSession(pg)
    port = adapter()

    async with session.begin():
        binding = await port.bind(
            session=session,
            context=evidence_context(),
            mode=TenantScopeMode.TENANT_SCOPED,
        )
        with pytest.raises(RlsContextError) as exc_info:
            async with port.narrow(
                session=session, binding=binding, cabinet_id=None, project_id=None
            ):
                pytest.fail("dropping the bound cabinet must never widen the scope")
        assert reason(exc_info) == "tenant_scope_widened"

        with pytest.raises(RlsContextError) as exc_info:
            async with port.narrow(
                session=session, binding=binding, cabinet_id=CABINET_B, project_id=None
            ):
                pytest.fail("replacing the bound cabinet must never move the scope")
        assert reason(exc_info) == "tenant_scope_widened"

        with pytest.raises(RlsContextError) as exc_info:
            async with port.narrow(
                session=session, binding=binding, cabinet_id=None, project_id=PROJECT_A
            ):
                pytest.fail("a project scope without a cabinet must be refused")
        assert reason(exc_info) == "tenant_context_invalid"
        # The refused narrow never wrote anything.
        assert pg.setting("app.cabinet_id") == CABINET_A
        assert pg.setting("app.project_id") == ""


@pytest.mark.asyncio
async def test_narrow_refuses_a_binding_from_another_transaction() -> None:
    pg = FakePostgres()
    session = FakeAsyncSession(pg)
    port = adapter()

    async with session.begin():
        binding = await port.bind(
            session=session,
            context=evidence_context(),
            mode=TenantScopeMode.TENANT_SCOPED,
        )

    async with session.begin():  # a new database transaction
        with pytest.raises(RlsContextError) as exc_info:
            async with port.narrow(
                session=session, binding=binding, cabinet_id=None, project_id=None
            ):
                pytest.fail("a binding must only be usable in its own transaction")
        assert reason(exc_info) == "binding_transaction_mismatch"


@pytest.mark.asyncio
async def test_narrow_refuses_a_foreign_object() -> None:
    pg = FakePostgres()
    session = FakeAsyncSession(pg)
    port = adapter()
    async with session.begin():
        await port.bind(
            session=session,
            context=evidence_context(),
            mode=TenantScopeMode.TENANT_SCOPED,
        )
        with pytest.raises(RlsContextError) as exc_info:
            async with port.narrow(
                session=session, binding={"tenant_id": TENANT_A}, cabinet_id=None, project_id=None  # type: ignore[arg-type]
            ):
                pytest.fail("only evidence produced by bind is accepted")
        assert reason(exc_info) == "tenant_context_invalid"
