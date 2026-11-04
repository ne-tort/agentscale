"""``SqlCapabilityScopePort``: fail-closed on authority *and* on the grant.

``allows`` answers "may this subject exercise this capability in this scope".
The answer is ``True`` only when two independent sources both agree:

1. the subject is a live member of a live tenant whose scope this provably is;
2. the persisted ``dynamic_app_grants`` row of the installation's published
   version carries a ``granted``, unexpired entry for the capability the
   manifest binds to the invoked method.

Every case below removes exactly one of those conditions — or supplies a shape
that cannot prove it — and asserts ``False``.  Nothing here asserts a ``True``
that a missing condition could also have produced: the happy path is asserted
once, in the first test, so every subsequent ``False`` is a real refusal.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import update

from prodavan.application.dynamic_app_plugin_gateway.contracts import (
    PluginHandle,
    PluginInvokeRequest,
)
from prodavan.domain.dynamic_apps import AppScope, AppScopeKind
from prodavan.infrastructure.persistence.models.cabinets import (
    CabinetCompanyGrantRow,
    CabinetEmployeeAssignmentRow,
    CabinetInstanceRow,
)
from prodavan.infrastructure.persistence.models.dynamic_apps import (
    AppDefinitionRow,
    AppGrantRow,
    AppInstanceRow,
    AppVersionRow,
)
from prodavan.infrastructure.persistence.models.identity import (
    CompanyRow,
    EmployeeRow,
    MembershipRow,
)
from prodavan.infrastructure.persistence.models.projects import ProjectRow

from .conftest import GatewayHarness
from .seeds import (
    NOW,
    cabinet,
    cabinet_assignment,
    cabinet_grant,
    company,
    employee,
    grant,
    membership,
    project,
    seed_baseline,
    seed_gateway_with_identity,
    seed_identity,
)
from .sqlite_mirror import (
    APP_ID,
    CABINET_A,
    CABINET_B,
    DEFINITION_A,
    DIGEST_A,
    EMPLOYEE_A,
    EMPLOYEE_B,
    GRANT_A,
    INSTANCE_A,
    MANIFEST_VERSION,
    PROJECT_A,
    PROJECT_B,
    SUBJECT_A,
    SUBJECT_B,
    TENANT_A,
    TENANT_B,
    VERSION_A,
)

TENANT_SCOPE = AppScope(kind=AppScopeKind.TENANT, id=TENANT_A)
CABINET_SCOPE = AppScope(kind=AppScopeKind.CABINET, id=CABINET_A)
PROJECT_SCOPE = AppScope(kind=AppScopeKind.PROJECT, id=PROJECT_A)


def invoke_request(*, method: str = "kv.put") -> PluginInvokeRequest:
    return PluginInvokeRequest(handle=PluginHandle(), method=method)


async def allows(
    gateway: GatewayHarness,
    *,
    tenant_id: str = TENANT_A,
    subject_id: str = SUBJECT_A,
    scope: AppScope = TENANT_SCOPE,
    method: str = "kv.put",
) -> bool:
    return await gateway.scope.allows(
        tenant_id=tenant_id,
        subject_id=subject_id,
        scope=scope,
        request=invoke_request(method=method),
    )


async def set_grant_entries(
    gateway: GatewayHarness, entries: list[dict], *, row_id: str = GRANT_A
) -> None:
    async with gateway.session_factory() as session:
        await session.execute(
            update(AppGrantRow).where(AppGrantRow.id == row_id).values(grants=entries)
        )
        await session.commit()


# ---------------------------------------------------------------------------
# The single positive case
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "scope",
    [TENANT_SCOPE, CABINET_SCOPE, PROJECT_SCOPE],
    ids=["tenant", "cabinet", "project"],
)
async def test_a_live_member_with_a_granted_capability_is_allowed(
    gateway: GatewayHarness, scope: AppScope
) -> None:
    """All three supported scope kinds, each with its own installation."""

    await seed_gateway_with_identity(gateway.session_factory)
    if scope.kind is not AppScopeKind.TENANT:
        await seed_scoped_installation(gateway, scope)

    assert await allows(gateway, scope=scope) is True


async def test_the_subject_may_be_stated_by_employee_id_or_subject_claim(
    gateway: GatewayHarness,
) -> None:
    """``subject_id`` is ``employees.id`` for the principal resolver and the OIDC
    ``sub`` for an auth boundary; both are values this repository stores."""

    await seed_gateway_with_identity(gateway.session_factory)

    assert await allows(gateway, subject_id=EMPLOYEE_A) is True
    assert await allows(gateway, subject_id=SUBJECT_A) is True


async def seed_scoped_installation(gateway: GatewayHarness, scope: AppScope) -> None:
    """A second *app*, installed in a cabinet/project scope instead of the tenant.

    A distinct ``app_id`` is required, not cosmetic: ``uq_dynamic_app_definition_
    tenant_app`` allows one definition per (tenant, app), so a second installation
    of the *same* app in another scope is not a state this schema can hold.
    """

    suffix = "cab" if scope.kind is AppScopeKind.CABINET else "prj"
    app_id = f"{suffix}-plugin"
    definition_id = f"app_{suffix}ccccccccccccccccc"
    version_id = f"aver_{suffix}cccccccccccccccc"
    instance_id = f"ains_{suffix}cccccccccccccccc"
    body = {
        "protocol": "1.0",
        "appId": app_id,
        "version": MANIFEST_VERSION,
        "displayName": f"{app_id} plugin",
        "requestedCapabilities": [{"name": "storage.kv", "reason": "persist drafts"}],
        "entrypoints": {
            "plugin": {
                "methods": [
                    {
                        "method": "kv.put",
                        "capability": "storage.kv",
                        "operation": "write",
                        "resource": "drafts",
                        "domain": "storage",
                    }
                ]
            }
        },
    }
    async with gateway.session_factory() as session:
        from .seeds import definition, instance, version

        session.add(
            definition(
                row_id=definition_id,
                tenant_id=TENANT_A,
                app_id=app_id,
                scope_kind=scope.kind.value,
                scope_id=scope.id,
                current_version_id=version_id,
            )
        )
        await session.flush()
        session.add(
            version(
                row_id=version_id,
                definition_id=definition_id,
                payload=body,
                digest="sha256:" + ("d" if suffix == "cab" else "e") * 64,
            )
        )
        await session.flush()
        session.add(
            grant(
                row_id=f"agr_{suffix}cccccccccccccccc",
                definition_id=definition_id,
                version_id=version_id,
                scope_kind=scope.kind.value,
                scope_id=scope.id,
            )
        )
        session.add(
            instance(
                row_id=instance_id,
                definition_id=definition_id,
                version_id=version_id,
                scope_kind=scope.kind.value,
                scope_id=scope.id,
            )
        )
        await session.commit()


# ---------------------------------------------------------------------------
# "grant absent" — the persisted decision is missing
# ---------------------------------------------------------------------------


async def test_no_grant_row_at_all_is_a_refusal(gateway: GatewayHarness) -> None:
    await seed_gateway_with_identity(gateway.session_factory)
    async with gateway.session_factory() as session:
        await session.execute(
            AppGrantRow.__table__.delete().where(AppGrantRow.id == GRANT_A)
        )
        await session.commit()

    assert await allows(gateway) is False


async def test_an_empty_grant_payload_is_a_refusal(gateway: GatewayHarness) -> None:
    await seed_gateway_with_identity(gateway.session_factory)
    await set_grant_entries(gateway, [])

    assert await allows(gateway) is False


async def test_an_explicitly_denied_capability_is_a_refusal(
    gateway: GatewayHarness,
) -> None:
    await seed_gateway_with_identity(gateway.session_factory)
    await set_grant_entries(
        gateway,
        [
            {
                "name": "storage.kv",
                "decision": "denied",
                "granted_by": "administrator",
            }
        ],
    )

    assert await allows(gateway) is False


async def test_a_grant_for_another_capability_is_a_refusal(
    gateway: GatewayHarness,
) -> None:
    await seed_gateway_with_identity(gateway.session_factory)
    await set_grant_entries(
        gateway,
        [
            {
                "name": "network.fetch",
                "decision": "granted",
                "granted_by": "administrator",
            }
        ],
    )

    assert await allows(gateway) is False


async def test_an_expired_capability_entry_is_a_refusal(
    gateway: GatewayHarness,
) -> None:
    await seed_gateway_with_identity(gateway.session_factory)
    await set_grant_entries(
        gateway,
        [
            {
                "name": "storage.kv",
                "decision": "granted",
                "granted_by": "administrator",
                "expires_at": (NOW - timedelta(minutes=1)).isoformat(),
            }
        ],
    )

    assert await allows(gateway) is False


async def test_an_expired_grant_row_is_a_refusal(gateway: GatewayHarness) -> None:
    await seed_gateway_with_identity(gateway.session_factory)
    async with gateway.session_factory() as session:
        await session.execute(
            update(AppGrantRow)
            .where(AppGrantRow.id == GRANT_A)
            .values(expires_at=NOW - timedelta(minutes=1))
        )
        await session.commit()

    assert await allows(gateway) is False


async def test_an_unexpired_grant_row_is_not_a_refusal(gateway: GatewayHarness) -> None:
    """The counterpart of the case above: a *live* expiry must still allow."""

    await seed_gateway_with_identity(gateway.session_factory)
    async with gateway.session_factory() as session:
        await session.execute(
            update(AppGrantRow)
            .where(AppGrantRow.id == GRANT_A)
            .values(expires_at=NOW + timedelta(minutes=5))
        )
        await session.commit()

    assert await allows(gateway) is True


async def test_a_malformed_grant_payload_is_a_refusal_not_an_exception(
    gateway: GatewayHarness,
) -> None:
    """A row that the CRUD writer would refuse must not authorize anyone.

    A database error is deliberately *not* in this class (the facade quarantines
    it as ``PORT_FAILED``); a malformed payload is data that cannot prove
    authority, so it is a plain refusal.
    """

    await seed_gateway_with_identity(gateway.session_factory)
    await set_grant_entries(gateway, [{"name": "not-a-capability"}])

    assert await allows(gateway) is False


# ---------------------------------------------------------------------------
# The method must bind exactly one capability
# ---------------------------------------------------------------------------


async def test_an_undeclared_method_is_a_refusal(gateway: GatewayHarness) -> None:
    await seed_gateway_with_identity(gateway.session_factory)

    assert await allows(gateway, method="kv.delete") is False


async def test_a_blank_method_is_a_refusal(gateway: GatewayHarness) -> None:
    await seed_gateway_with_identity(gateway.session_factory)

    assert await allows(gateway, method="  ") is False


async def test_a_method_holding_a_capability_without_a_granted_entry_is_a_refusal(
    gateway: GatewayHarness,
) -> None:
    """The manifest binds ``kv.put`` to ``storage.kv``; the grant grants kv *read*.

    The port asks whether the *bound capability* is granted, so a grant that
    covers a different method's capability must not leak across methods.
    """

    await seed_gateway_with_identity(gateway.session_factory)
    await set_grant_entries(
        gateway,
        [
            {
                "name": "identity.read",
                "decision": "granted",
                "granted_by": "administrator",
            }
        ],
    )

    assert await allows(gateway) is False


# ---------------------------------------------------------------------------
# The subject's own authority
# ---------------------------------------------------------------------------


async def test_an_unknown_subject_is_a_refusal(gateway: GatewayHarness) -> None:
    await seed_gateway_with_identity(gateway.session_factory)

    assert await allows(gateway, subject_id="kc-nobody") is False


async def test_a_blank_subject_is_a_refusal(gateway: GatewayHarness) -> None:
    await seed_gateway_with_identity(gateway.session_factory)

    assert await allows(gateway, subject_id="") is False
    assert await allows(gateway, subject_id="   ") is False


@pytest.mark.parametrize("status", ["invited", "disabled"])
async def test_an_inactive_employee_is_a_refusal(
    gateway: GatewayHarness, status: str
) -> None:
    await seed_gateway_with_identity(gateway.session_factory)
    async with gateway.session_factory() as session:
        await session.execute(
            update(EmployeeRow)
            .where(EmployeeRow.id == EMPLOYEE_A)
            .values(status=status)
        )
        await session.commit()

    assert await allows(gateway) is False


async def test_a_soft_deleted_employee_is_a_refusal(gateway: GatewayHarness) -> None:
    await seed_gateway_with_identity(gateway.session_factory)
    async with gateway.session_factory() as session:
        await session.execute(
            update(EmployeeRow)
            .where(EmployeeRow.id == EMPLOYEE_A)
            .values(deleted_at=NOW)
        )
        await session.commit()

    assert await allows(gateway) is False


async def test_an_employee_without_a_membership_is_a_refusal(
    gateway: GatewayHarness,
) -> None:
    await seed_gateway_with_identity(gateway.session_factory)
    async with gateway.session_factory() as session:
        await session.execute(MembershipRow.__table__.delete())
        await session.commit()

    assert await allows(gateway) is False


async def test_a_membership_in_a_different_tenant_is_a_refusal(
    gateway: GatewayHarness,
) -> None:
    await seed_gateway_with_identity(gateway.session_factory)
    await seed_identity(
        gateway.session_factory,
        company_id=TENANT_B,
        employee_id=EMPLOYEE_B,
        subject=SUBJECT_B,
        cabinet_id=CABINET_B,
        project_id=PROJECT_B,
    )
    async with gateway.session_factory() as session:
        await session.execute(MembershipRow.__table__.delete())
        session.add(membership(employee_id=EMPLOYEE_A, company_id=TENANT_B))
        await session.commit()

    # The subject is a member of tenant B only; asking as tenant A is refused.
    assert await allows(gateway) is False


async def test_a_deleted_company_is_a_refusal(gateway: GatewayHarness) -> None:
    await seed_gateway_with_identity(gateway.session_factory)
    async with gateway.session_factory() as session:
        await session.execute(
            update(CompanyRow)
            .where(CompanyRow.id == TENANT_A)
            .values(deleted_at=NOW)
        )
        await session.commit()

    assert await allows(gateway) is False


async def test_an_inactive_company_is_a_refusal(gateway: GatewayHarness) -> None:
    await seed_gateway_with_identity(gateway.session_factory)
    async with gateway.session_factory() as session:
        await session.execute(
            update(CompanyRow).where(CompanyRow.id == TENANT_A).values(status="suspended")
        )
        await session.commit()

    assert await allows(gateway) is False


async def test_an_unknown_tenant_is_a_refusal(gateway: GatewayHarness) -> None:
    await seed_gateway_with_identity(gateway.session_factory)

    assert await allows(gateway, tenant_id="comp_nothere") is False


async def test_a_blank_tenant_is_a_refusal(gateway: GatewayHarness) -> None:
    await seed_gateway_with_identity(gateway.session_factory)

    assert await allows(gateway, tenant_id="") is False


# ---------------------------------------------------------------------------
# "another tenant" — the scope is not yours
# ---------------------------------------------------------------------------


async def test_a_member_of_one_tenant_cannot_use_another_tenants_scope(
    gateway: GatewayHarness,
) -> None:
    await seed_gateway_with_identity(gateway.session_factory)
    await seed_identity(
        gateway.session_factory,
        company_id=TENANT_B,
        employee_id=EMPLOYEE_B,
        subject=SUBJECT_B,
        cabinet_id=CABINET_B,
        project_id=PROJECT_B,
    )

    # Subject A asking for tenant B's scope, even though tenant B exists and is
    # active and subject A holds a grant in their own tenant.
    assert await allows(gateway, tenant_id=TENANT_B, subject_id=SUBJECT_A) is False


async def test_a_tenant_scope_id_that_is_not_the_tenant_is_a_refusal(
    gateway: GatewayHarness,
) -> None:
    await seed_gateway_with_identity(gateway.session_factory)

    # Tenant A, but a scope naming tenant B: not this tenant's scope.
    assert (
        await allows(
            gateway, scope=AppScope(kind=AppScopeKind.TENANT, id=TENANT_B)
        )
        is False
    )


async def test_an_ungranted_cabinet_is_a_refusal(gateway: GatewayHarness) -> None:
    """The cabinet exists and is active, but no grant links it to the tenant."""

    await seed_identity(gateway.session_factory)
    await seed_baseline(gateway.session_factory)
    async with gateway.session_factory() as session:
        await session.execute(
            update(CabinetInstanceRow)
            .where(CabinetInstanceRow.id == CABINET_A)
            .values(company_grant_scope="selected")
        )
        await session.execute(CabinetCompanyGrantRow.__table__.delete())
        await session.commit()

    assert await allows(gateway, scope=CABINET_SCOPE) is False


async def test_a_cabinet_owned_by_another_tenant_is_a_refusal(
    gateway: GatewayHarness,
) -> None:
    await seed_gateway_with_identity(gateway.session_factory)
    await seed_scoped_installation(gateway, CABINET_SCOPE)
    async with gateway.session_factory() as session:
        await session.execute(
            update(CabinetInstanceRow)
            .where(CabinetInstanceRow.id == CABINET_A)
            .values(owner_scope="company", owner_company_id=TENANT_B)
        )
        await session.execute(CabinetCompanyGrantRow.__table__.delete())
        await session.commit()

    assert await allows(gateway, scope=CABINET_SCOPE) is False


async def test_a_cabinet_granted_to_everyone_still_needs_the_assignment(
    gateway: GatewayHarness,
) -> None:
    """``company_grant_scope='all'`` is a grant; a *revoked* assignment overrides it."""

    await seed_gateway_with_identity(gateway.session_factory)
    await seed_scoped_installation(gateway, CABINET_SCOPE)
    async with gateway.session_factory() as session:
        await session.execute(
            update(CabinetInstanceRow)
            .where(CabinetInstanceRow.id == CABINET_A)
            .values(company_grant_scope="all")
        )
        await session.commit()
    assert await allows(gateway, scope=CABINET_SCOPE) is True

    async with gateway.session_factory() as session:
        await session.execute(
            update(CabinetEmployeeAssignmentRow)
            .where(CabinetEmployeeAssignmentRow.employee_id == EMPLOYEE_A)
            .values(status="revoked")
        )
        await session.commit()

    assert await allows(gateway, scope=CABINET_SCOPE) is False


@pytest.mark.parametrize("status", ["archived", "deleted"])
async def test_an_archived_cabinet_is_a_refusal(
    gateway: GatewayHarness, status: str
) -> None:
    await seed_gateway_with_identity(gateway.session_factory)
    await seed_scoped_installation(gateway, CABINET_SCOPE)
    async with gateway.session_factory() as session:
        await session.execute(
            update(CabinetInstanceRow)
            .where(CabinetInstanceRow.id == CABINET_A)
            .values(status=status)
        )
        await session.commit()

    assert await allows(gateway, scope=CABINET_SCOPE) is False


async def test_a_foreign_project_is_a_refusal(gateway: GatewayHarness) -> None:
    await seed_gateway_with_identity(gateway.session_factory)
    await seed_scoped_installation(gateway, PROJECT_SCOPE)
    async with gateway.session_factory() as session:
        await session.execute(
            update(ProjectRow)
            .where(ProjectRow.id == PROJECT_A)
            .values(company_id=TENANT_B)
        )
        await session.commit()

    assert await allows(gateway, scope=PROJECT_SCOPE) is False


async def test_a_non_active_project_is_a_refusal(gateway: GatewayHarness) -> None:
    await seed_gateway_with_identity(gateway.session_factory)
    await seed_scoped_installation(gateway, PROJECT_SCOPE)
    async with gateway.session_factory() as session:
        await session.execute(
            update(ProjectRow)
            .where(ProjectRow.id == PROJECT_A)
            .values(status="completed")
        )
        await session.commit()

    assert await allows(gateway, scope=PROJECT_SCOPE) is False


async def test_a_project_whose_cabinet_is_not_granted_is_a_refusal(
    gateway: GatewayHarness,
) -> None:
    await seed_gateway_with_identity(gateway.session_factory)
    await seed_scoped_installation(gateway, PROJECT_SCOPE)
    async with gateway.session_factory() as session:
        await session.execute(CabinetCompanyGrantRow.__table__.delete())
        await session.commit()

    assert await allows(gateway, scope=PROJECT_SCOPE) is False


# ---------------------------------------------------------------------------
# "archived instance" — lifecycle gates the grant, not the scope
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("status", ["disabled", "revoked", "draft"])
async def test_a_non_published_instance_is_a_refusal(
    gateway: GatewayHarness, status: str
) -> None:
    await seed_gateway_with_identity(gateway.session_factory)
    async with gateway.session_factory() as session:
        await session.execute(
            update(AppInstanceRow)
            .where(AppInstanceRow.id == INSTANCE_A)
            .values(status=status)
        )
        await session.commit()

    assert await allows(gateway) is False


async def test_a_non_published_version_is_a_refusal(gateway: GatewayHarness) -> None:
    await seed_gateway_with_identity(gateway.session_factory)
    async with gateway.session_factory() as session:
        await session.execute(
            update(AppVersionRow)
            .where(AppVersionRow.id == VERSION_A)
            .values(status="disabled")
        )
        await session.commit()

    assert await allows(gateway) is False


async def test_a_grant_belonging_to_another_version_does_not_apply(
    gateway: GatewayHarness,
) -> None:
    """The grant is read for the *instance's* version, not for "a version that has one".

    Reachable shape: one definition with two published versions, the grant issued
    only for the newer one, and the instance still pinned to the older.  Pointing
    the instance at a *non-existent* version would be unreachable — the mirrored
    ``(app_version_id, app_definition_id)`` foreign key forbids it — and a fixture
    that models an impossible row proves nothing about the query.
    """

    await seed_gateway_with_identity(gateway.session_factory)
    async with gateway.session_factory() as session:
        from .seeds import version

        session.add(
            version(
                row_id="aver_aaaaaaaaaaaaaaaaabbb",
                definition_id=DEFINITION_A,
                manifest_version="2.0.0",
                digest="sha256:" + "9" * 64,
                payload={
                    "protocol": "1.0",
                    "appId": APP_ID,
                    "version": "2.0.0",
                    "displayName": "Demo plugin",
                    "requestedCapabilities": [
                        {"name": "storage.kv", "reason": "persist drafts"}
                    ],
                    "entrypoints": {
                        "plugin": {
                            "methods": [
                                {
                                    "method": "kv.put",
                                    "capability": "storage.kv",
                                    "operation": "write",
                                    "resource": "drafts",
                                    "domain": "storage",
                                }
                            ]
                        }
                    },
                },
            )
        )
        await session.flush()
        # The only grant lives on the *newer* version.
        await session.execute(
            update(AppGrantRow)
            .where(AppGrantRow.id == GRANT_A)
            .values(app_version_id="aver_aaaaaaaaaaaaaaaaabbb")
        )
        await session.commit()

    # The instance is still on version A, which now has no grant of its own.
    assert await allows(gateway) is False


# ---------------------------------------------------------------------------
# Ambiguity — the port signature cannot answer, so it refuses
# ---------------------------------------------------------------------------


async def test_two_installations_disagreeing_about_one_method_are_a_refusal(
    gateway: GatewayHarness,
) -> None:
    """Two installations in one scope, one granting and one denying the capability.

    ``allows`` receives a *method name*, not an instance id, so a genuine tie is
    unanswerable.  Answering the positive one would be a guess with an
    authorization consequence.
    """

    await seed_gateway_with_identity(gateway.session_factory)
    # A second app installed in the same tenant scope, bound to the same method
    # name but with the capability denied.  A distinct ``app_id`` is required by
    # ``uq_dynamic_app_definition_tenant_app``; the *method name* collision is the
    # point of the case, and it is perfectly expressible.
    async with gateway.session_factory() as session:
        from .seeds import definition, instance, version

        session.add(
            definition(
                row_id="app_dddddddddddddddddddd",
                tenant_id=TENANT_A,
                app_id="denying-plugin",
                scope_kind="tenant",
                scope_id=TENANT_A,
                current_version_id="aver_dddddddddddddddddddd",
            )
        )
        await session.flush()
        session.add(
            version(
                row_id="aver_dddddddddddddddddddd",
                definition_id="app_dddddddddddddddddddd",
                digest="sha256:" + "f" * 64,
                payload={
                    "protocol": "1.0",
                    "appId": "denying-plugin",
                    "version": MANIFEST_VERSION,
                    "displayName": "Denying",
                    "requestedCapabilities": [
                        {"name": "storage.kv", "reason": "persist"}
                    ],
                    "entrypoints": {
                        "plugin": {
                            "methods": [
                                {
                                    "method": "kv.put",
                                    "capability": "storage.kv",
                                    "operation": "write",
                                    "resource": "drafts",
                                    "domain": "storage",
                                }
                            ]
                        }
                    },
                },
            )
        )
        await session.flush()
        session.add(
            grant(
                row_id="agr_dddddddddddddddddddd",
                definition_id="app_dddddddddddddddddddd",
                version_id="aver_dddddddddddddddddddd",
                scope_kind="tenant",
                scope_id=TENANT_A,
                entries=[
                    {
                        "name": "storage.kv",
                        "decision": "denied",
                        "granted_by": "administrator",
                    }
                ],
            )
        )
        session.add(
            instance(
                row_id="ains_dddddddddddddddddd",
                definition_id="app_dddddddddddddddddddd",
                version_id="aver_dddddddddddddddddddd",
                scope_kind="tenant",
                scope_id=TENANT_A,
            )
        )
        await session.commit()

    assert await allows(gateway) is False


async def test_two_installations_both_granting_is_not_ambiguous(
    gateway: GatewayHarness,
) -> None:
    """A unanimous positive is answerable; refusing it would deny a real grant."""

    await seed_gateway_with_identity(gateway.session_factory)
    async with gateway.session_factory() as session:
        from .seeds import definition, instance, version

        session.add(
            definition(
                row_id="app_dddddddddddddddddddd",
                tenant_id=TENANT_A,
                app_id="second-plugin",
                scope_kind="tenant",
                scope_id=TENANT_A,
                current_version_id="aver_dddddddddddddddddddd",
            )
        )
        await session.flush()
        session.add(
            version(
                row_id="aver_dddddddddddddddddddd",
                definition_id="app_dddddddddddddddddddd",
                payload={
                    "protocol": "1.0",
                    "appId": "second-plugin",
                    "version": MANIFEST_VERSION,
                    "displayName": "Second",
                    "requestedCapabilities": [{"name": "storage.kv", "reason": "x"}],
                    "entrypoints": {
                        "plugin": {
                            "methods": [
                                {
                                    "method": "kv.put",
                                    "capability": "storage.kv",
                                    "operation": "write",
                                    "resource": "drafts",
                                    "domain": "storage",
                                }
                            ]
                        }
                    },
                },
                digest="sha256:" + "f" * 64,
            )
        )
        await session.flush()
        session.add(
            grant(
                row_id="agr_dddddddddddddddddddd",
                definition_id="app_dddddddddddddddddddd",
                version_id="aver_dddddddddddddddddddd",
                scope_kind="tenant",
                scope_id=TENANT_A,
            )
        )
        session.add(
            instance(
                row_id="ains_dddddddddddddddddd",
                definition_id="app_dddddddddddddddddddd",
                version_id="aver_dddddddddddddddddddd",
                scope_kind="tenant",
                scope_id=TENANT_A,
            )
        )
        await session.commit()

    assert await allows(gateway) is True


async def test_an_invalid_scope_object_is_a_refusal(gateway: GatewayHarness) -> None:
    await seed_gateway_with_identity(gateway.session_factory)

    assert (
        await gateway.scope.allows(
            tenant_id=TENANT_A,
            subject_id=SUBJECT_A,
            scope=object(),  # type: ignore[arg-type]
            request=invoke_request(),
        )
        is False
    )


async def test_an_invalid_request_object_is_a_refusal(gateway: GatewayHarness) -> None:
    await seed_gateway_with_identity(gateway.session_factory)

    assert (
        await gateway.scope.allows(
            tenant_id=TENANT_A,
            subject_id=SUBJECT_A,
            scope=TENANT_SCOPE,
            request=object(),  # type: ignore[arg-type]
        )
        is False
    )


__all__: tuple[str, ...] = ()
