"""Unit tests for plugin source HTTP contracts; not run during freeze."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any, cast

import pytest
from fastapi import Response
from pydantic import BaseModel, ValidationError

from prodavan.api.v1 import plugin_sources as api
from prodavan.application.plugins.sources.service import (
    ModuleSourceProvisionResult,
    ModuleSourceProvisioningConfig,
    ModuleSourceService,
    ModuleSourceUnavailableError,
    ModuleSourceValidationError,
)
from prodavan.application.plugins.sources.types import (
    ModuleSource,
    ModuleSourceStatus,
    ModuleSourceTrustConfig,
)
from prodavan.domain.identity import Principal
from prodavan.domain.plugins.helpers import (
    PLUGIN_INSTANCE_ID_PATTERN,
    plugin_instance_id_for,
)
from prodavan.infrastructure.plugins.sources.models import (
    BranchProtectionPolicy,
    ManagedOrganization,
    RepositoryTemplate,
)

from ..domain._fixtures import valid_manifest_dict

NOW = datetime(2026, 11, 9, 12, 0, tzinfo=UTC)
PATH = "/companies/{company_id}/plugins/{plugin_id}/source"
SCOPE_PATH = PATH + "/scope"


def _source() -> ModuleSource:
    return ModuleSource(
        id="psrc_0123456789abcdef",
        company_id="co_123",
        plugin_id="mod_equipment",
        repo_id=123,
        full_name="managed-plugins/plugin-mod_equipment-0123456789abcdef",
        private=True,
        default_branch="main",
        installation_credential_ref="vault://private/github/installation",
        trusted_workflow_ref="ne-tort/agentscale/.github/workflows/plugin-artifacts.yml@" + "a" * 40,
        status=ModuleSourceStatus.READY,
        created_at=NOW,
        updated_at=NOW,
    )


class _State:
    def __init__(self, **values: object) -> None:
        for key, value in values.items():
            setattr(self, key, value)


class _App:
    def __init__(self, state: _State) -> None:
        self.state = state


class _Request:
    def __init__(self, app: _App) -> None:
        self.app = app


class FakeService:
    def __init__(self, *, created: bool) -> None:
        self.created = created
        self.create_calls: list[dict[str, Any]] = []
        self.status_calls: list[dict[str, Any]] = []
        self.scope_calls: list[dict[str, Any]] = []

    async def create_from_template(self, **kwargs: Any) -> ModuleSourceProvisionResult:
        self.create_calls.append(kwargs)
        return ModuleSourceProvisionResult(source=_source(), created=self.created)

    async def get_status(self, **kwargs: Any) -> ModuleSource:
        self.status_calls.append(kwargs)
        return _source()

    async def bind_scope(self, **kwargs: Any) -> ModuleSource:
        self.scope_calls.append(kwargs)
        return replace(
            _source(),
            cabinet_id=kwargs["cabinet_id"],
            project_id=kwargs["project_id"],
        )


class _AllowEntitlement:
    async def require_company_actor(
        self,
        principal: Principal,
        company_id: str,
        *,
        employee: object,
    ) -> object:
        return SimpleNamespace(id=company_id)


class _MutationSentinel:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def __getattr__(self, name: str) -> Any:
        async def forbidden(*args: Any, **kwargs: Any) -> None:
            self.calls.append(name)
            raise AssertionError(f"unexpected source mutation: {name}")

        return forbidden


def _request(service: object) -> _Request:
    return _Request(
        _App(_State(module_source_service_factory=lambda session: service))
    )


def test_router_has_create_status_and_scope_routes() -> None:
    actual = {
        (route.path, method)
        for route in api.router.routes
        for method in (route.methods or ())
    }
    assert len(api.router.routes) == 3
    assert {(PATH, "POST"), (PATH, "GET"), (SCOPE_PATH, "PUT")} <= actual
    assert actual <= {(PATH, "POST"), (PATH, "GET"), (PATH, "HEAD"), (SCOPE_PATH, "PUT")}


def test_request_and_response_models_are_extra_forbid() -> None:
    for model in (api.CreateModuleSourceBody, api.BindModuleSourceScopeBody, api.ModuleSourceResponse):
        assert issubclass(model, BaseModel)
        assert model.model_config.get("extra") == "forbid"
    with pytest.raises(ValidationError):
        api.CreateModuleSourceBody(
            manifest={}, organization="attacker-controlled"  # type: ignore[call-arg]
        )


def test_missing_composition_is_typed_503() -> None:
    request = _Request(_App(_State()))
    with pytest.raises(ModuleSourceUnavailableError) as caught:
        api._service(cast(Any, request), cast(Any, object()))
    assert caught.value.status == 503
    assert caught.value.code == "MODULE_SOURCE_NOT_CONFIGURED"


def test_real_factory_uses_request_entitlement_and_owned_repository_transactions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = object()
    session_factory = object()
    monkeypatch.setattr(api, "get_session_factory", lambda: session_factory)
    monkeypatch.setattr(
        api,
        "SqlModuleSourceRepository",
        lambda **kwargs: ("repository", kwargs),
    )
    monkeypatch.setattr(
        api, "EntitlementService", lambda actual: ("entitlement", actual)
    )
    monkeypatch.setattr(api, "ModuleSourceService", lambda **kwargs: kwargs)
    factory = api.make_module_source_service_factory(
        provider=cast(Any, object()), config=cast(Any, object())
    )

    built = cast(Any, factory)(cast(Any, session))

    assert built["repository"] == (
        "repository",
        {"session_factory": session_factory},
    )
    assert built["entitlement"] == ("entitlement", session)
    assert built["project_access"].__class__ is api.ProjectAccessPolicy


async def test_create_returns_201_and_no_store() -> None:
    service = FakeService(created=True)
    response = Response()
    result = await api.create_module_source(
        company_id="co_123",
        plugin_id="mod_equipment",
        body=api.CreateModuleSourceBody(manifest={"plugin_id": "mod_equipment"}),
        request=cast(Any, _request(service)),
        response=response,
        principal=cast(Principal, object()),
        session=cast(Any, object()),
        employee=None,
    )

    assert response.status_code == 201
    assert response.headers["Cache-Control"] == "no-store"
    assert result.status is ModuleSourceStatus.READY
    assert service.create_calls[0]["company_id"] == "co_123"


async def test_api_foreign_deterministic_instance_is_422_before_provider_writes() -> None:
    provider = _MutationSentinel()
    repository = _MutationSentinel()
    source = _source()
    template = RepositoryTemplate(
        owner="managed-plugins",
        name="private-plugin-template",
        agents_revision="a" * 40,
        security_revision="b" * 40,
    )
    service = ModuleSourceService(
        provider=cast(Any, provider),
        repository=cast(Any, repository),
        entitlement=_AllowEntitlement(),
        config=ModuleSourceProvisioningConfig(
            organization=ManagedOrganization(
                name="managed-plugins",
                installation_id=42,
                template=template,
            ),
            trust=ModuleSourceTrustConfig(
                installation_credential_ref=source.installation_credential_ref,
                trusted_workflow_ref=source.trusted_workflow_ref,
            ),
            branch_protection=BranchProtectionPolicy(
                required_status_checks=("plugin/verify",),
            ),
        ),
    )
    foreign_instance = plugin_instance_id_for("co_other", source.plugin_id)
    manifest = valid_manifest_dict()
    manifest.update(
        plugin_id=source.plugin_id,
        egress_ref=f"grant:{source.company_id}/{source.plugin_id}/inst_source",
        pods={
            "allowlist": [
                {
                    "plugin_instance_id": foreign_instance,
                    "project_id": "project_a",
                    "job_id": "job_a",
                }
            ]
        },
        secret_delivery={
            "mode": "k8s_secret",
            "allowlist": [
                "vault://ai_keys/plugins/capabilities/secrets/"
                f"{source.company_id}/{source.plugin_id}/{foreign_instance}/api-key"
            ],
        },
    )

    with pytest.raises(ModuleSourceValidationError) as caught:
        await api.create_module_source(
            company_id=source.company_id,
            plugin_id=source.plugin_id,
            body=api.CreateModuleSourceBody(manifest=manifest),
            request=cast(Any, _request(service)),
            response=Response(),
            principal=cast(Principal, object()),
            session=cast(Any, object()),
            employee=None,
        )

    assert caught.value.status == 422
    assert caught.value.code == "MODULE_SOURCE_INSTANCE_SCOPE_MISMATCH"
    assert provider.calls == []
    assert repository.calls == []


async def test_repeat_returns_200_and_status_is_no_store() -> None:
    service = FakeService(created=False)
    response = Response()
    await api.create_module_source(
        company_id="co_123",
        plugin_id="mod_equipment",
        body=api.CreateModuleSourceBody(manifest={"plugin_id": "mod_equipment"}),
        request=cast(Any, _request(service)),
        response=response,
        principal=cast(Principal, object()),
        session=cast(Any, object()),
        employee=None,
    )
    assert response.status_code == 200

    status_response = Response()
    result = await api.get_module_source_status(
        company_id="co_123",
        plugin_id="mod_equipment",
        request=cast(Any, _request(service)),
        response=status_response,
        principal=cast(Principal, object()),
        session=cast(Any, object()),
        employee=None,
    )
    assert status_response.status_code == 200
    assert status_response.headers["Cache-Control"] == "no-store"
    assert result.id == "psrc_0123456789abcdef"


async def test_bind_scope_uses_typed_body_and_returns_exact_scope() -> None:
    service = FakeService(created=False)
    response = Response()
    result = await api.bind_module_source_scope(
        company_id="co_123",
        plugin_id="mod_equipment",
        body=api.BindModuleSourceScopeBody(
            cabinet_id="cab_123", project_id="pr_123"
        ),
        request=cast(Any, _request(service)),
        response=response,
        principal=cast(Principal, object()),
        session=cast(Any, object()),
        employee=None,
    )
    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "no-store"
    assert result.cabinet_id == "cab_123"
    assert result.project_id == "pr_123"
    assert service.scope_calls[0]["cabinet_id"] == "cab_123"
    assert service.scope_calls[0]["project_id"] == "pr_123"


def test_response_contains_metadata_and_provenance_but_no_secret_ref() -> None:
    response = api.ModuleSourceResponse.from_source(_source())
    payload = response.model_dump()
    encoded = response.model_dump_json()

    assert "installation_credential_ref" not in response.model_fields
    assert "manifest" not in response.model_fields
    assert "token" not in response.model_fields
    assert payload["plugin_instance_id"] == plugin_instance_id_for(
        _source().company_id,
        _source().plugin_id,
    )
    assert PLUGIN_INSTANCE_ID_PATTERN.fullmatch(payload["plugin_instance_id"])
    assert payload["expected_ref"] == "refs/heads/main"
    assert payload["expected_signer_identity"].endswith("@" + "a" * 40)
    assert "vault://private/github/installation" not in encoded
    assert "credential" not in encoded
