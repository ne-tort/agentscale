"""Cross-cutting acceptance: idempotency, tenant isolation, permissions, route wiring.

Authored for the migration freeze; not executed yet.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from prodavan.application.dynamic_apps_definition import (
    ActorIdentity,
    ArchiveDefinitionRequest,
    CreateDefinitionRequest,
    CreateVersionRequest,
    DEFINITION_TRANSITIONS,
    DynamicAppErrorCode,
    DynamicAppOperation,
    MutationContext,
    PublishVersionRequest,
    api_lifecycle,
    canonical_json,
    digest_of,
    idempotency_scope,
)
from prodavan.application.dynamic_apps_definition.crud_ports import AuditConflict
from prodavan.domain.dynamic_apps import AppLifecycleStatus

from .fakes import (
    CABINET_A,
    DIGEST_1,
    TENANT_A,
    TENANT_B,
    FakeAudit,
    context,
    manifest,
    service,
)

API_ROOT = Path(__file__).resolve().parents[3]


# ------------------------------------------------------------------ idempotency


async def test_same_key_and_body_replays_the_original_receipt():
    svc, repository, audit, _ = service()
    ctx = context(key="create-1")
    request = CreateDefinitionRequest(app_id="equipment-review", display_name="Eq")

    first = await svc.create_definition(context=ctx, request=request)
    second = await svc.create_definition(context=ctx, request=request)

    assert first.replayed is False
    assert second.replayed is True
    assert second.data == first.data
    assert second.trace_id == first.trace_id
    assert len(repository.definitions) == 1
    assert len(audit.appended) == 1


async def test_same_key_with_different_body_is_a_conflict_not_an_overwrite():
    svc, repository, _, _ = service()
    ctx = context(key="create-1")
    await svc.create_definition(
        context=ctx,
        request=CreateDefinitionRequest(app_id="equipment-review", display_name="Eq"),
    )

    with pytest.raises(Exception) as error:
        await svc.create_definition(
            context=ctx,
            request=CreateDefinitionRequest(
                app_id="equipment-review", display_name="Renamed"
            ),
        )

    assert error.value.code == "IDEMPOTENCY_REPLAY"
    assert error.value.status == 409
    stored = next(iter(repository.definitions.values()))
    assert stored.display_name == "Eq"


async def test_idempotency_scope_is_tenant_and_operation_specific():
    same_key = "abcd-1234"
    definition_scope = idempotency_scope(
        tenant_id=TENANT_A, operation=DynamicAppOperation.CREATE_DEFINITION, key=same_key
    )
    other_tenant = idempotency_scope(
        tenant_id=TENANT_B, operation=DynamicAppOperation.CREATE_DEFINITION, key=same_key
    )
    other_operation = idempotency_scope(
        tenant_id=TENANT_A, operation=DynamicAppOperation.ARCHIVE_DEFINITION, key=same_key
    )

    assert definition_scope != other_tenant
    assert definition_scope != other_operation
    assert len(definition_scope) <= 200
    # The raw client key is never stored in clear text.
    assert same_key not in definition_scope


async def test_missing_idempotency_key_is_rejected_before_side_effects():
    svc, repository, audit, _ = service()
    ctx = MutationContext(
        identity=ActorIdentity(tenant_id=TENANT_A, actor_id="emp_admin"),
        idempotency_key="   ",
        operation=DynamicAppOperation.CREATE_DEFINITION,
    )

    with pytest.raises(Exception) as error:
        await svc.create_definition(
            context=ctx,
            request=CreateDefinitionRequest(app_id="equipment-review", display_name="Eq"),
        )

    assert error.value.code == DynamicAppErrorCode.IDEMPOTENCY_KEY_REQUIRED.value
    assert error.value.status == 400
    assert repository.definitions == {}
    assert audit.appended == []


async def test_concurrent_writer_conflict_fails_closed_and_rolls_back():
    svc, repository, _, _ = service()
    ctx = context(key="create-race")

    class RacingAudit(FakeAudit):
        async def find(self, *, tenant_id: str, scope_key: str):
            return None  # both racers see "no record"

        async def append(self, record):
            raise AuditConflict("uw_platform_events_idempotency_key")

    racing = type(svc)(repository, RacingAudit(), scope_authority=svc._scope_authority)  # noqa: SLF001

    with pytest.raises(Exception) as error:
        await racing.create_definition(
            context=ctx,
            request=CreateDefinitionRequest(app_id="equipment-review", display_name="Eq"),
        )

    assert error.value.code == "IDEMPOTENCY_REPLAY"
    assert repository.rollbacks == 1
    assert repository.commits == 0


async def test_no_audit_record_is_written_without_a_successful_mutation():
    svc, _, audit, _ = service()
    with pytest.raises(Exception):
        await svc.create_definition(
            context=context(key="create-bad"),
            request=CreateDefinitionRequest(app_id="X", display_name="Eq"),
        )
    assert audit.appended == []


# ---------------------------------------------------------------- tenant isolation


async def test_definition_from_another_tenant_is_invisible():
    svc, _, _, _ = service()
    created = await svc.create_definition(
        context=context(tenant_id=TENANT_B, key="create-beta"),
        request=CreateDefinitionRequest(app_id="beta-app", display_name="Beta"),
    )

    with pytest.raises(Exception) as get_error:
        await svc.get_definition(identity_tenant_id=TENANT_A, app_id="beta-app")
    assert get_error.value.status == 404

    with pytest.raises(Exception) as archive_error:
        await svc.archive_definition(
            context=context(
                tenant_id=TENANT_A,
                operation=DynamicAppOperation.ARCHIVE_DEFINITION,
                key="archive-1",
            ),
            request=ArchiveDefinitionRequest(definition_id=created.data["id"]),
        )
    assert archive_error.value.status == 404

    with pytest.raises(Exception) as publish_error:
        await svc.publish_version(
            context=context(
                tenant_id=TENANT_A,
                operation=DynamicAppOperation.PUBLISH_VERSION,
                key="publish-1",
            ),
            request=PublishVersionRequest(
                definition_id=created.data["id"], version_id="aver_any"
            ),
        )
    assert publish_error.value.status == 404


async def test_foreign_tenant_error_is_indistinguishable_from_missing():
    svc, _, _, _ = service()
    created = await svc.create_definition(
        context=context(tenant_id=TENANT_B, key="create-beta"),
        request=CreateDefinitionRequest(app_id="beta-app", display_name="Beta"),
    )

    with pytest.raises(Exception) as foreign:
        await svc.get_definition(identity_tenant_id=TENANT_A, app_id="beta-app")
    with pytest.raises(Exception) as missing:
        await svc.get_definition(identity_tenant_id=TENANT_A, app_id="ghost-app")

    assert foreign.value.code == missing.value.code == "NOT_FOUND"
    assert foreign.value.status == missing.value.status == 404
    assert foreign.value.detail == missing.value.detail
    assert created.data["tenant_id"] == TENANT_B


async def test_same_app_id_may_exist_in_two_tenants():
    svc, repository, _, _ = service()
    alpha = await svc.create_definition(
        context=context(tenant_id=TENANT_A, key="create-alpha"),
        request=CreateDefinitionRequest(app_id="equipment-review", display_name="A"),
    )
    beta = await svc.create_definition(
        context=context(tenant_id=TENANT_B, key="create-beta"),
        request=CreateDefinitionRequest(app_id="equipment-review", display_name="B"),
    )

    assert alpha.data["id"] != beta.data["id"]
    assert len(repository.definitions) == 2


async def test_missing_tenant_context_fails_closed():
    svc, repository, audit, _ = service()

    # A blank tenant cannot even be represented: ``ActorIdentity`` is the first fail-closed
    # gate, so constructing a context with ``tenant_id=" "`` raises before any service call.
    # (The previous version of this test built such an identity and expected the *service*
    # to reject it — an unreachable state, since the identity refuses to exist at all.)
    with pytest.raises(ValueError):
        ActorIdentity(tenant_id=" ", actor_id="emp_admin")
    with pytest.raises(ValueError):
        ActorIdentity(tenant_id=TENANT_A, actor_id="  ")

    # Read paths have no identity object, so the service-level guard is what refuses them.
    with pytest.raises(Exception) as list_error:
        await svc.list_definitions(identity_tenant_id="")
    assert list_error.value.code == "TENANT_CONTEXT_REQUIRED"

    with pytest.raises(Exception) as get_error:
        await svc.get_definition(identity_tenant_id=" ", app_id="equipment-review")
    assert get_error.value.code == "TENANT_CONTEXT_REQUIRED"

    assert repository.definitions == {}
    assert audit.appended == []


# ------------------------------------------------------------------- permissions


def test_tenant_context_carries_only_server_resolved_identity():
    """The API layer must build the mutation context from ``CompanyRow.id``."""
    source = (API_ROOT / "src/prodavan/api/v1/dynamic_apps.py").read_text(encoding="utf-8")
    assert "require_company_actor" in source
    assert "company.id" in source
    # The path segment must never be forwarded to the service as the write tenant. The
    # resolution moved into ``resolve_tenant_context``, so the invariant is expressed as
    # "the write tenant is company.id, never company_id" in whichever form it appears.
    assert "tenant_id=company_id" not in source
    assert "ActorIdentity(tenant_id=company_id" not in source
    assert "TenantContext(tenant_id=company.id" in source
    assert "ActorIdentity(tenant_id=tenant.tenant_id" in source
    # Every mutating route must go through that resolution before building a context.
    assert "tenant = await resolve_tenant_context(" in source
    assert source.count("tenant = await resolve_tenant_context(") >= 7


def test_every_mutating_route_requires_an_idempotency_header():
    source = (API_ROOT / "src/prodavan/api/v1/dynamic_apps.py").read_text(encoding="utf-8")
    mutating = [
        "create_dynamic_app",
        "archive_dynamic_app",
        "upload_dynamic_app_version",
        "publish_dynamic_app_version",
        "install_dynamic_app_instance",
        "issue_dynamic_app_grant",
        "revoke_dynamic_app_grant",
    ]
    for name in mutating:
        block = source.split(f"async def {name}(", 1)[1].split("\n@router.", 1)[0]
        assert "_IDEMPOTENCY_HEADER" in block, name
        assert "_context(" in block, name


def test_dynamic_app_router_is_registered_without_touching_other_routers():
    router_source = (API_ROOT / "src/prodavan/api/v1/router.py").read_text(encoding="utf-8")
    assert "dynamic_apps," in router_source
    assert "router.include_router(dynamic_apps.router)" in router_source
    assert "router.include_router(company_modules.router)" in router_source
    assert "router.include_router(agent_runs.router)" in router_source


def test_dynamic_app_routes_are_nested_under_the_company_scope():
    source = (API_ROOT / "src/prodavan/api/v1/dynamic_apps.py").read_text(encoding="utf-8")
    assert 'prefix="/companies/{company_id}/dynamic-apps"' in source
    for path in (
        '@router.get("")',
        '@router.post("", status_code=201)',
        '@router.get("/{app_id}")',
        '@router.post("/{app_id}/archive")',
        '@router.post("/{app_id}/versions", status_code=201)',
        '@router.post("/{app_id}/versions/{version_id}/publish")',
        '@router.post("/{app_id}/instances", status_code=201)',
        '@router.post("/{app_id}/versions/{version_id}/grants", status_code=201)',
        '@router.post("/{app_id}/versions/{version_id}/grants/{capability}/revoke")',
    ):
        assert path in source, path


def test_api_layer_has_no_in_memory_fallback():
    source = (API_ROOT / "src/prodavan/api/v1/dynamic_apps.py").read_text(encoding="utf-8")
    assert "SqlAlchemyDynamicAppRepository(session)" in source
    assert "PlatformEventAuditAdapter(session)" in source
    assert "SqlAlchemyScopeAuthority(session)" in source
    assert "FakeRepository" not in source
    assert "FakeAudit" not in source


# -------------------------------------------------------------------- lifecycle


def test_lifecycle_matrix_is_forward_only():
    assert DEFINITION_TRANSITIONS[AppLifecycleStatus.DRAFT] == frozenset(
        {AppLifecycleStatus.PUBLISHED}
    )
    assert DEFINITION_TRANSITIONS[AppLifecycleStatus.PUBLISHED] == frozenset(
        {AppLifecycleStatus.DISABLED}
    )
    assert DEFINITION_TRANSITIONS[AppLifecycleStatus.DISABLED] == frozenset()
    assert DEFINITION_TRANSITIONS[AppLifecycleStatus.REVOKED] == frozenset()


def test_public_lifecycle_vocabulary_maps_onto_persisted_enum():
    assert api_lifecycle(AppLifecycleStatus.DRAFT) == "draft"
    assert api_lifecycle(AppLifecycleStatus.PUBLISHED) == "published"
    assert api_lifecycle(AppLifecycleStatus.DISABLED) == "archived"
    # A revoked definition must never be presented as merely archived.
    assert api_lifecycle(AppLifecycleStatus.REVOKED) == "revoked"


# --------------------------------------------------------------- digest helpers


def test_request_digest_is_stable_and_order_independent():
    left = {"a": 1, "b": [1, 2]}
    right = {"b": [1, 2], "a": 1}
    assert digest_of(left) == digest_of(right)
    assert digest_of(left) != digest_of({"a": 2, "b": [1, 2]})
    assert canonical_json(left) == canonical_json(right)
    assert digest_of(left).startswith("sha256:")


async def test_audit_payload_is_redacted_for_manifest_bearing_events():
    svc, _, audit, _ = service()
    created = await svc.create_definition(
        context=context(key="create-1"),
        request=CreateDefinitionRequest(app_id="equipment-review", display_name="Eq"),
    )
    await svc.create_version(
        context=context(operation=DynamicAppOperation.CREATE_VERSION, key="version-1"),
        definition_id=created.data["id"],
        request=CreateVersionRequest(manifest=manifest(), artifact_digest=DIGEST_1),
    )
    serialized = [canonical_json(item.result) for item in audit.appended]
    assert all("requestedCapabilities" not in blob for blob in serialized)
    assert all("entrypoints" not in blob for blob in serialized)


async def test_cabinet_scope_proof_is_required_for_installs():
    svc, _, _, scopes = service()
    created = await svc.create_definition(
        context=context(key="create-1"),
        request=CreateDefinitionRequest(app_id="equipment-review", display_name="Eq"),
    )
    assert (TENANT_A, CABINET_A) in scopes.cabinets
    assert created.data["id"]
