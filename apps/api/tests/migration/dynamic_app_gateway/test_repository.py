"""``SqlDynamicPluginRepository``: exact resolution, tenant scoping, no guessing.

Every case states a database shape and asserts the *identical* answer a legacy
owner would give: either the exact installation, or ``None`` (never a
neighbouring row), or an ambiguity the facade can quarantine.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy.exc import IntegrityError

from prodavan.application.dynamic_app_plugin_gateway.contracts import PluginOpenRequest
from prodavan.domain.dynamic_apps import AppLifecycleStatus, AppScope, AppScopeKind
from prodavan.infrastructure.persistence.dynamic_app_gateway import AmbiguousPluginRecord
from prodavan.infrastructure.persistence.models.dynamic_apps import AppGrantRow

from .conftest import GatewayHarness, gateway_with
from .seeds import NOW, definition, seed_baseline, seed_second_tenant, version
from .sqlite_mirror import (
    APP_ID,
    DEFINITION_A,
    DEFINITION_B,
    DIGEST_A,
    GRANT_A,
    INSTANCE_A,
    MANIFEST_VERSION,
    TENANT_A,
    TENANT_B,
    VERSION_A,
    VERSION_B,
)


def open_request(
    *,
    tenant_id: str = TENANT_A,
    app_id: str = APP_ID,
    manifest_version: str = MANIFEST_VERSION,
    scope: AppScope | None = None,
    subject_id: str = "kc-subject-aaaa",
) -> PluginOpenRequest:
    return PluginOpenRequest(
        tenant_id=tenant_id,
        app_id=app_id,
        manifest_version=manifest_version,
        scope=scope or AppScope(kind=AppScopeKind.TENANT, id=tenant_id),
        subject_id=subject_id,
    )


# ---------------------------------------------------------------------------
# "grant exists" — the happy path
# ---------------------------------------------------------------------------


async def test_resolve_returns_the_exact_installation(gateway: GatewayHarness) -> None:
    await seed_baseline(gateway.session_factory)

    record = await gateway.repository.resolve(open_request())

    assert record is not None
    assert record.definition.id == DEFINITION_A
    assert record.definition.tenant_id == TENANT_A
    assert record.definition.app_id == APP_ID
    assert record.definition.status is AppLifecycleStatus.PUBLISHED
    assert record.version.id == VERSION_A
    assert record.version.manifest_version == MANIFEST_VERSION
    assert record.version.artifact_digest == DIGEST_A
    assert record.instance.id == INSTANCE_A
    assert record.instance.status is AppLifecycleStatus.PUBLISHED
    # Grants are resolved for the same exact scope, and the payload is decoded
    # exactly as the CRUD adapter persists it.
    assert len(record.grants) == 1
    assert record.grants[0].scope == record.definition.scope
    assert [entry.name.value for entry in record.grants[0].grants] == ["storage.kv"]
    # Methods come from the facade's own parser, not a second local one.
    assert [method.method for method in record.methods] == ["kv.put"]


async def test_resolve_returns_aware_timestamps_only(gateway: GatewayHarness) -> None:
    """The domain refuses naive datetimes; the adapter must not surface them."""

    await seed_baseline(gateway.session_factory)

    record = await gateway.repository.resolve(open_request())

    assert record is not None
    for moment in (
        record.definition.created_at,
        record.definition.updated_at,
        record.version.created_at,
        record.instance.created_at,
        record.grants[0].issued_at,
    ):
        assert moment.tzinfo is not None, moment
        assert moment.utcoffset() is not None, moment


async def test_get_grants_and_get_instance_are_tenant_scoped(
    gateway: GatewayHarness,
) -> None:
    await seed_baseline(gateway.session_factory)
    scope = AppScope(kind=AppScopeKind.TENANT, id=TENANT_A)

    grants = await gateway.repository.get_grants(
        instance_id=INSTANCE_A, app_version_id=VERSION_A, tenant_id=TENANT_A
    )
    found = await gateway.repository.get_instance(
        instance_id=INSTANCE_A, scope=scope, tenant_id=TENANT_A
    )

    assert [row.scope for row in grants] == [scope]
    assert found is not None and found.id == INSTANCE_A
    # The protocol signature carries no tenant; the derived answer is the same.
    assert len(await gateway.repository.get_grants(instance_id=INSTANCE_A, app_version_id=VERSION_A)) == 1
    assert (
        await gateway.repository.get_instance(instance_id=INSTANCE_A, scope=scope)
    ).id == INSTANCE_A


# ---------------------------------------------------------------------------
# "no grant" / nothing there — a refusal, never a neighbour
# ---------------------------------------------------------------------------


async def test_resolve_answers_none_when_the_app_does_not_exist(
    gateway: GatewayHarness,
) -> None:
    await seed_baseline(gateway.session_factory)

    assert await gateway.repository.resolve(open_request(app_id="another-app")) is None


async def test_resolve_answers_none_for_an_unknown_manifest_version(
    gateway: GatewayHarness,
) -> None:
    await seed_baseline(gateway.session_factory)

    assert await gateway.repository.resolve(open_request(manifest_version="9.9.9")) is None


async def test_resolve_answers_none_when_there_is_no_instance_in_the_scope(
    gateway: GatewayHarness,
) -> None:
    """A definition/version with no installation is not an installation."""

    await seed_baseline(gateway.session_factory)
    other_scope = AppScope(kind=AppScopeKind.CABINET, id="cab_aaaaaaaaaaaaaaaaaaaa")

    assert await gateway.repository.resolve(open_request(scope=other_scope)) is None


async def test_an_empty_grant_row_yields_no_capability_authority(
    gateway: GatewayHarness,
) -> None:
    """An installation with a grant row carrying no entries resolves, but grants nothing.

    This is the "grant absent" shape that must *not* be confused with "record
    absent": the facade answers ``GRANTS_MISSING``/``GRANT_DENIED`` for it, which
    is a different, auditable outcome from ``APP_MISSING``.
    """

    await seed_baseline(gateway.session_factory)
    async with gateway.session_factory() as session:
        row = await session.get(
            __import__(
                "prodavan.infrastructure.persistence.models.dynamic_apps",
                fromlist=["AppGrantRow"],
            ).AppGrantRow,
            "agr_aaaaaaaaaaaaaaaaaaaa",
        )
        row.grants = []
        await session.commit()

    record = await gateway.repository.resolve(open_request())

    assert record is not None
    assert record.grants and record.grants[0].grants == ()


# ---------------------------------------------------------------------------
# "another tenant" — the row is unreachable, not merely filtered
# ---------------------------------------------------------------------------


async def test_another_tenant_cannot_read_the_installation(
    gateway: GatewayHarness,
) -> None:
    await seed_baseline(gateway.session_factory)
    await seed_second_tenant(gateway.session_factory)

    # Tenant B asks for tenant A's app/version/scope.
    intruder = await gateway.repository.resolve(
        open_request(
            tenant_id=TENANT_B,
            scope=AppScope(kind=AppScopeKind.TENANT, id=TENANT_A),
        )
    )
    assert intruder is None

    # …and cannot fetch the other installation by id either.
    assert (
        await gateway.repository.get_instance(
            instance_id=INSTANCE_A,
            scope=AppScope(kind=AppScopeKind.TENANT, id=TENANT_A),
            tenant_id=TENANT_B,
        )
        is None
    )
    assert (
        await gateway.repository.get_grants(
            instance_id=INSTANCE_A, app_version_id=VERSION_A, tenant_id=TENANT_B
        )
        == ()
    )

    # Each tenant still sees exactly its own record.
    own_b = await gateway.repository.resolve(
        open_request(
            tenant_id=TENANT_B, scope=AppScope(kind=AppScopeKind.TENANT, id=TENANT_B)
        )
    )
    assert own_b is not None and own_b.definition.id == DEFINITION_B
    assert own_b.version.id == VERSION_B


async def test_a_version_from_another_definition_is_not_returned(
    gateway: GatewayHarness,
) -> None:
    """A version id that belongs to a different definition answers "no grants"."""

    await seed_baseline(gateway.session_factory)
    await seed_second_tenant(gateway.session_factory)

    assert (
        await gateway.repository.get_grants(
            instance_id=INSTANCE_A, app_version_id=VERSION_B, tenant_id=TENANT_A
        )
        == ()
    )


# ---------------------------------------------------------------------------
# "archived/disabled instance" — reported, not hidden
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("status", ["disabled", "revoked", "draft"])
async def test_a_non_published_instance_is_still_resolved(
    gateway: GatewayHarness, status: str
) -> None:
    """Lifecycle is a policy answer, so the record must reach the facade.

    Returning ``None`` here would rewrite ``LIFECYCLE_DENIED`` into
    ``APP_MISSING`` and the shadow evidence would stop matching the legacy owner
    it is supposed to reproduce.
    """

    await seed_baseline(gateway.session_factory)
    async with gateway.session_factory() as session:
        from prodavan.infrastructure.persistence.models.dynamic_apps import AppInstanceRow

        row = await session.get(AppInstanceRow, INSTANCE_A)
        row.status = status
        await session.commit()

    record = await gateway.repository.resolve(open_request())

    assert record is not None, "a non-published installation must still be visible"
    assert record.instance.status.value == status


async def test_a_disabled_definition_is_still_resolved(gateway: GatewayHarness) -> None:
    await seed_baseline(gateway.session_factory)
    async with gateway.session_factory() as session:
        from prodavan.infrastructure.persistence.models.dynamic_apps import AppDefinitionRow

        row = await session.get(AppDefinitionRow, DEFINITION_A)
        row.status = "disabled"
        await session.commit()

    record = await gateway.repository.resolve(open_request())

    assert record is not None
    assert record.definition.status is AppLifecycleStatus.DISABLED


# ---------------------------------------------------------------------------
# Ambiguity — refused, never guessed
# ---------------------------------------------------------------------------


async def test_two_definitions_for_one_app_are_refused_not_guessed() -> None:
    """The unique key makes this impossible in a migrated database; if it happens
    anyway (a dropped constraint, a restore, a hand-written row), picking a row
    would authorize the wrong app — so the port refuses instead.

    The schema is created *without* ``uq_dynamic_app_definition_tenant_app`` on
    purpose: with the constraint installed the state under test cannot exist, and
    a test that never reaches the guard proves nothing about it.  A companion
    test below asserts the constraint really does make it unreachable in the
    normal schema.
    """

    async with gateway_with(
        without_unique=frozenset({"uq_dynamic_app_definition_tenant_app"})
    ) as harness:
        await seed_baseline(harness.session_factory)
        async with harness.session_factory() as session:
            session.add(
                definition(
                    row_id="app_cccccccccccccccccccc",
                    app_id=APP_ID,
                    current_version_id="aver_cccccccccccccccccccc",
                )
            )
            await session.flush()
            session.add(
                version(
                    row_id="aver_cccccccccccccccccccc",
                    definition_id="app_cccccccccccccccccccc",
                    digest="sha256:" + "c" * 64,
                )
            )
            await session.commit()

        with pytest.raises(AmbiguousPluginRecord):
            await harness.repository.resolve(open_request())


async def test_the_unique_key_makes_a_second_definition_unreachable(
    gateway: GatewayHarness,
) -> None:
    """With the real constraint, the ambiguous state cannot be written at all.

    Both halves matter: the constraint prevents the state in a healthy database,
    and the guard refuses it in a damaged one.  Asserting only the guard would
    leave the constraint unverified; asserting only the constraint would leave
    the guard untested.
    """

    await seed_baseline(gateway.session_factory)
    async with gateway.session_factory() as session:
        session.add(
            definition(
                row_id="app_cccccccccccccccccccc",
                app_id=APP_ID,
                current_version_id=None,
            )
        )
        with pytest.raises(IntegrityError):
            await session.commit()
        await session.rollback()

    # Exactly one definition survived, so the port resolves it.
    record = await gateway.repository.resolve(open_request())
    assert record is not None and record.definition.id == DEFINITION_A


async def test_two_grant_rows_for_one_scope_are_refused() -> None:
    """Two rows for one version+scope would be two conflicting decisions."""

    async with gateway_with(
        without_unique=frozenset({"uq_dynamic_app_grant_version_scope"})
    ) as harness:
        await seed_baseline(harness.session_factory)
        async with harness.session_factory() as session:
            session.add(
                AppGrantRow(
                    id="agr_bbbbbbbbbbbbbbbbbbbb",
                    app_definition_id=DEFINITION_A,
                    app_version_id=VERSION_A,
                    scope_kind="tenant",
                    scope_id=TENANT_A,
                    grants=[],
                    issued_at=NOW,
                )
            )
            await session.commit()

        with pytest.raises(AmbiguousPluginRecord):
            await harness.repository.resolve(open_request())
        with pytest.raises(AmbiguousPluginRecord):
            await harness.repository.get_grants(
                instance_id=INSTANCE_A, app_version_id=VERSION_A, tenant_id=TENANT_A
            )


async def test_one_grant_row_per_scope_is_the_normal_shape(
    gateway: GatewayHarness,
) -> None:
    """The unique key is what lets the port treat "two rows" as an anomaly."""

    await seed_baseline(gateway.session_factory)
    async with gateway.session_factory() as session:
        session.add(
            AppGrantRow(
                id="agr_bbbbbbbbbbbbbbbbbbbb",
                app_definition_id=DEFINITION_A,
                app_version_id=VERSION_A,
                scope_kind="tenant",
                scope_id=TENANT_A,
                grants=[],
                issued_at=NOW,
            )
        )
        with pytest.raises(IntegrityError):
            await session.commit()
        await session.rollback()

    rows = await gateway.repository.get_grants(
        instance_id=INSTANCE_A, app_version_id=VERSION_A, tenant_id=TENANT_A
    )
    assert len(rows) == 1

    async with gateway.session_factory() as session:
        row = await session.get(AppGrantRow, GRANT_A)
        assert row is not None
        # Expiry is decided by the facade against its own ``now``; hiding an
        # expired row here would move the decision into the adapter and
        # desynchronise the two owners it exists to compare.
        row.expires_at = NOW + timedelta(minutes=5)
        await session.commit()

    record = await gateway.repository.resolve(open_request())
    assert record is not None
    assert record.grants[0].expires_at is not None


# ---------------------------------------------------------------------------
# The port must not invent a record out of malformed input
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "request_kwargs",
    [
        {"tenant_id": ""},
        {"tenant_id": "   "},
        {"app_id": ""},
        {"manifest_version": ""},
        {"subject_id": ""},
    ],
)
async def test_a_blank_request_never_resolves(
    gateway: GatewayHarness, request_kwargs: dict[str, str]
) -> None:
    """A contract that was never filled in must not read the whole table.

    ``AppScope`` is built from a *fixed* tenant id here: the point of the case is
    the blank field under test, and a scope derived from a blank tenant would
    raise inside pydantic before the port ever ran — proving nothing about the
    port.  (An empty ``PluginOpenRequest`` is still expressible, because the
    contract is a plain dataclass precisely so a malformed caller is the port's
    problem to refuse, not a construction error to crash on.)
    """

    await seed_baseline(gateway.session_factory)

    request = open_request(
        scope=AppScope(kind=AppScopeKind.TENANT, id=TENANT_A), **request_kwargs
    )
    assert await gateway.repository.resolve(request) is None


async def test_the_repository_never_writes(gateway: GatewayHarness) -> None:
    """A read port that mutates would make the shadow comparison meaningless."""

    await seed_baseline(gateway.session_factory)
    from prodavan.infrastructure.persistence.models.dynamic_apps import (
        AppDefinitionRow,
        AppGrantRow,
        AppInstanceRow,
        AppVersionRow,
    )

    async with gateway.session_factory() as session:
        before = {
            model.__tablename__: len((await session.execute(__import__("sqlalchemy").select(model))).scalars().all())
            for model in (AppDefinitionRow, AppVersionRow, AppGrantRow, AppInstanceRow)
        }

    await gateway.repository.resolve(open_request())
    await gateway.repository.get_grants(instance_id=INSTANCE_A, app_version_id=VERSION_A)
    await gateway.repository.get_instance(
        instance_id=INSTANCE_A, scope=AppScope(kind=AppScopeKind.TENANT, id=TENANT_A)
    )

    async with gateway.session_factory() as session:
        after = {
            model.__tablename__: len((await session.execute(__import__("sqlalchemy").select(model))).scalars().all())
            for model in (AppDefinitionRow, AppVersionRow, AppGrantRow, AppInstanceRow)
        }
    assert before == after


__all__: tuple[str, ...] = ()
