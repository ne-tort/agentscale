"""Server-side contract extraction: routes, response fields and error statuses.

Nothing here is a hand-written copy of the API. Every set is derived from the live
objects the application serves:

* routes and parameters — ``app.openapi()`` on an app assembled from the real
  routers through ``include_router``;
* response field names — the very serializer functions / pydantic models that
  build the response body (``_definition_view``, ``ResourceView.as_payload``,
  ``AssetResource`` …). A field the caller reads but no serializer emits cannot
  appear here by accident.
* error statuses — the stable error-code enums and the raise sites that use them.
"""

from __future__ import annotations

import ast
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path
from typing import Any

from fastapi import FastAPI

API_PREFIX = "/api/v1"

_REPO_ROOT = Path(__file__).resolve().parents[5]
API_SRC = _REPO_ROOT / "apps" / "api" / "src" / "prodavan"
WEB_SRC = _REPO_ROOT / "apps" / "web" / "src"


# ----------------------------------------------------------------------------- app


@lru_cache(maxsize=1)
def build_app() -> FastAPI:
    """Mount the three routers under review exactly as ``api/v1/router.py`` does."""
    from prodavan.api.v1 import content, dynamic_apps, resources

    app = FastAPI()
    app.include_router(dynamic_apps.router, prefix=API_PREFIX)
    app.include_router(content.router, prefix=API_PREFIX)
    app.include_router(resources.router, prefix=API_PREFIX)
    return app


@lru_cache(maxsize=1)
def openapi() -> dict[str, Any]:
    return build_app().openapi()


def operations() -> dict[tuple[str, str], Mapping[str, Any]]:
    """``(METHOD, path-template) -> operation object`` for every registered route."""
    found: dict[tuple[str, str], Mapping[str, Any]] = {}
    for path, item in openapi()["paths"].items():
        for method, operation in item.items():
            found[(method.upper(), path)] = operation
    return found


def path_parameters(path: str) -> tuple[str, ...]:
    return tuple(re.findall(r"\{([^}]+)\}", path))


# ------------------------------------------------------------------ path matching

_METHODS = frozenset({"get", "post", "put", "patch", "delete"})


def _segments(path: str) -> list[str]:
    return [segment for segment in path.split("/") if segment]


def _is_parameter(segment: str) -> bool:
    return segment.startswith("{") and segment.endswith("}")


def route_matches(call_path: str, route_path: str) -> bool:
    """True when a concrete caller path matches a server template.

    ``{param}`` in the server template matches exactly one non-empty segment; the
    caller's own interpolations are ordinary segments, so this reduces to a
    segment-wise comparison with parameter positions wildcarded.
    """
    left, right = _segments(call_path), _segments(route_path)
    if len(left) != len(right):
        return False
    return all(
        _is_parameter(server) or caller == server
        for caller, server in zip(left, right, strict=True)
    )


@dataclass(frozen=True, slots=True)
class Call:
    """One HTTP operation the web client declares it performs."""

    method: str
    path: str
    source: str


def _iter_ts_sources(root: Path) -> Iterable[Path]:
    for path in sorted(root.rglob("*.ts")):
        if path.name.endswith((".test.ts", ".d.ts")):
            continue
        yield path


# ------------------------------------------------------------------- response fields


def _sample_definition():
    from prodavan.domain.dynamic_apps import AppDefinition, AppLifecycleStatus, AppScope, AppScopeKind

    now = datetime(2026, 1, 1, tzinfo=UTC)
    return AppDefinition(
        id="adef_sample",
        tenant_id="co_sample",
        app_id="sample-app",
        display_name="Sample",
        description=None,
        scope=AppScope(kind=AppScopeKind.TENANT, id="co_sample"),
        status=AppLifecycleStatus.PUBLISHED,
        current_version_id="aver_sample",
        created_at=now,
        updated_at=now,
    )


def _sample_version():
    from prodavan.domain.dynamic_apps import AppLifecycleStatus, AppVersion

    now = datetime(2026, 1, 1, tzinfo=UTC)
    return AppVersion(
        id="aver_sample",
        app_definition_id="adef_sample",
        manifest_version="1.0.0",
        artifact_digest="sha256:" + "0" * 64,
        manifest={
            "protocol": "1.0",
            "appId": "sample-app",
            "displayName": "Sample",
            "version": "1.0.0",
            "requestedCapabilities": [],
            "entrypoints": {"main": "index.html"},
        },
        status=AppLifecycleStatus.PUBLISHED,
        created_at=now,
        published_at=now,
    )


def _sample_instance():
    from prodavan.domain.dynamic_apps import (
        AppInstance,
        AppLifecycleStatus,
        AppScope,
        AppScopeKind,
    )

    now = datetime(2026, 1, 1, tzinfo=UTC)
    return AppInstance(
        id="ains_sample",
        app_definition_id="adef_sample",
        app_version_id="aver_sample",
        scope=AppScope(kind=AppScopeKind.CABINET, id="cab_sample"),
        status=AppLifecycleStatus.PUBLISHED,
        runtime_ref=None,
        created_at=now,
        updated_at=now,
    )


def _sample_grant():
    from prodavan.domain.dynamic_apps import (
        AppGrant,
        AppScope,
        AppScopeKind,
        CapabilityDecision,
        CapabilityGrant,
        CapabilityName,
        GrantActor,
    )

    now = datetime(2026, 1, 1, tzinfo=UTC)
    return AppGrant(
        id="agrn_sample",
        app_definition_id="adef_sample",
        app_version_id="aver_sample",
        scope=AppScope(kind=AppScopeKind.CABINET, id="cab_sample"),
        grants=(
            CapabilityGrant(
                name=CapabilityName.UI_RENDER,
                decision=CapabilityDecision.GRANTED,
                granted_by=GrantActor.ADMINISTRATOR,
            ),
        ),
        issued_at=now,
    )


def dynamic_app_fields() -> dict[str, set[str]]:
    """Field names per dynamic-app entity, taken from the response serializers."""
    from prodavan.application.dynamic_apps_definition.crud_service import (
        _definition_view,
        _grant_view,
        _instance_view,
        _version_view,
    )

    return {
        "definition": set(_definition_view(_sample_definition())),
        "version": set(_version_view(_sample_version())),
        "instance": set(_instance_view(_sample_instance())),
        "grant": set(_grant_view(_sample_grant())),
    }


def resource_fields() -> dict[str, set[str]]:
    """Field names per canonical-resource entity, taken from ``as_payload``."""
    from prodavan.application.resources.api.contracts import (
        MaterializationApplyView,
        MaterializationPlanView,
        ResourceProvenanceView,
        ResourceView,
        ScopeView,
        VersionView,
    )

    scope = ScopeView(kind="project", id="prj_sample")
    provenance = ResourceProvenanceView(
        source_type="project_runtime",
        source_id="prj_sample",
        source_generation=1,
        digest="sha256:" + "1" * 64,
    )
    resource = ResourceView(
        id="res_sample",
        kind="prompt",
        slug="sample",
        title="Sample",
        description="",
        scope=scope,
        lifecycle="published",
        active_version=1,
        version_numbers=(1,),
        binding_count=0,
        created_at=None,
        updated_at=None,
        provenance=provenance,
    )
    version = VersionView(
        id="rver_sample",
        resource_id="res_sample",
        number=1,
        status="published",
        label="",
        change_note="",
        payload={"kind": "prompt", "body": "x", "variables": []},
        digest="sha256:" + "2" * 64,
        created_at=None,
        created_by=None,
    )
    plan = MaterializationPlanView(
        id="plan_sample",
        project_id="prj_sample",
        desired_generation=2,
        expected_generation=1,
        applied_generation=None,
        status="planned",
        digest="sha256:" + "3" * 64,
        idempotency_key="key",
        snapshot_digest="sha256:" + "4" * 64,
        entries=(),
        blockers=(),
        created_at="2026-01-01T00:00:00+00:00",
    )
    apply_view = MaterializationApplyView(
        plan_id="plan_sample",
        project_id="prj_sample",
        outcome="applied",
        expected_generation=2,
        desired_generation=2,
        applied_generation=2,
    )
    return {
        "resource": set(resource.as_payload()),
        "version": set(version.as_payload()),
        "plan": set(plan.as_payload()),
        "apply": set(apply_view.as_payload()),
    }


def _model_fields(model: type) -> tuple[set[str], set[str]]:
    """``(all fields, required fields)`` for a pydantic response model."""
    schema = model.model_json_schema()
    properties = set(schema.get("properties", {}))
    return properties, set(schema.get("required", []))


def content_fields() -> dict[str, tuple[set[str], set[str]]]:
    """``(fields, required)`` per content response model."""
    from prodavan.application.content import schemas

    wanted = {
        "asset": schemas.AssetResource,
        "asset_list": schemas.AssetList,
        "version": schemas.VersionResource,
        "begin_version": schemas.BeginVersionResponse,
        "version_list": schemas.VersionList,
        "acl": schemas.AclList,
        "acl_entry": schemas.AclEntry,
        "dependents": schemas.AssetDependents,
        "alias": schemas.AliasResource,
        "link": schemas.AssetLink,
        "abort": schemas.VersionAbortResult,
    }
    return {name: _model_fields(model) for name, model in wanted.items()}


# ---------------------------------------------------------------- vocabularies


def resource_vocabulary() -> dict[str, frozenset[str]]:
    """Closed value sets the canonical resource API accepts and emits."""
    from prodavan.application.migration.resources.lifecycle_contracts import (
        BindingKind,
        ResourceLifecycle,
        ResourceKind,
        ScopeKind,
        VersionStatus,
    )
    from prodavan.application.resources.api import contracts as resource_contracts

    # ``ResourceKind`` in the migration contracts also carries the pre-MIG values
    # (connection/credential/configuration) that the HTTP surface refuses; the
    # creatable-on-the-wire set is the explicit constant, so that is what a client
    # may rely on.
    creatable = resource_contracts.RESOURCE_KINDS
    assert creatable <= _enum_values(ResourceKind), (
        "RESOURCE_KINDS must be a subset of the domain ResourceKind enum"
    )
    return {
        "resource_kind": frozenset(creatable),
        "owner_scope_kind": frozenset(resource_contracts.OWNER_SCOPE_KINDS),
        "target_scope_kind": frozenset(resource_contracts.TARGET_SCOPE_KINDS),
        "scope_kind": frozenset(_enum_values(ScopeKind)),
        "lifecycle": frozenset(_enum_values(ResourceLifecycle)),
        "version_status": frozenset(_enum_values(VersionStatus)),
        "binding_mode": frozenset(_enum_values(BindingKind)),
        "binding_kind": frozenset(resource_contracts.BINDING_KINDS),
        "plan_status": frozenset({"ready", "blocked"}),
        "plan_entry_status": frozenset({"planned"}),
    }


def content_vocabulary() -> dict[str, frozenset[str]]:
    """Closed value sets the Content API accepts and emits."""
    from prodavan.application.content.contracts import (
        ContentLinkKind,
        ContentPrincipalKind,
        ContentVisibility,
    )

    return {
        "principal_kind": frozenset(_enum_values(ContentPrincipalKind)),
        "visibility": frozenset(_enum_values(ContentVisibility)),
        "link_kind": frozenset(_enum_values(ContentLinkKind)),
        "permission": frozenset({"read", "write", "admin"}),
    }


def dynamic_vocabulary() -> dict[str, frozenset[str]]:
    """Closed value sets the dynamic-app surface emits."""
    from prodavan.application.dynamic_apps_definition.lifecycle import API_LIFECYCLE
    from prodavan.application.dynamic_apps_definition.lifecycle import api_lifecycle
    from prodavan.domain.dynamic_apps import AppLifecycleStatus, AppScopeKind, CapabilityDecision

    # Every lifecycle string the serializer can produce, derived by asking it rather
    # than by copying `API_LIFECYCLE`: a `revoked` row is outside the CRUD surface's
    # writable vocabulary but `api_lifecycle` still maps it, so it can reach a client.
    emitted = frozenset(api_lifecycle(status) for status in AppLifecycleStatus)
    assert set(API_LIFECYCLE) <= emitted, (
        "API_LIFECYCLE must be a subset of what api_lifecycle() emits"
    )
    return {
        "lifecycle": emitted,
        "scope": frozenset(_enum_values(AppScopeKind)) - {"tenant"},
        "decision": frozenset(_enum_values(CapabilityDecision)),
    }


# ------------------------------------------------------------------- error statuses


def _enum_values(enum: type) -> set[str]:
    return {member.value for member in enum}


@dataclass(frozen=True, slots=True)
class ServerErrors:
    """Stable codes and the HTTP statuses the three surfaces can answer with."""

    dynamic_codes: frozenset[str]
    resource_codes: frozenset[str]
    content_codes: frozenset[str]
    statuses: frozenset[int]


@lru_cache(maxsize=1)
def server_errors() -> ServerErrors:
    from prodavan.application.content.contracts import ContentErrorCode
    from prodavan.application.dynamic_apps_definition.crud_contracts import DynamicAppErrorCode
    from prodavan.application.resources.api.errors import (
        _STATUS_AND_TITLE,
        ResourceApiErrorCode,
    )

    assert set(_STATUS_AND_TITLE) == set(ResourceApiErrorCode), (
        "every resource error code must declare an HTTP status"
    )
    statuses: set[int] = {status for status, _ in _STATUS_AND_TITLE.values()}

    # The dynamic-app and content surfaces raise statuses inline at each site instead of
    # going through a code->status table, so the literal at every raise site is the only
    # honest source. Whole packages are scanned: a status raised from one module of a
    # package is still answerable through the router that package backs.
    statuses |= _literal_statuses(
        API_SRC / "application" / "dynamic_apps_definition",
        API_SRC / "api" / "v1" / "dynamic_apps.py",
    )
    statuses |= _literal_statuses(
        API_SRC / "application" / "content",
        API_SRC / "application" / "resources",
        API_SRC / "api" / "v1" / "content.py",
        API_SRC / "api" / "v1" / "resources.py",
        API_SRC / "domain" / "errors.py",
    )

    return ServerErrors(
        dynamic_codes=frozenset(_enum_values(DynamicAppErrorCode)),
        resource_codes=frozenset(_enum_values(ResourceApiErrorCode)),
        content_codes=frozenset(_enum_values(ContentErrorCode)),
        statuses=frozenset(statuses),
    )


def _literal_statuses(*targets: Path) -> set[int]:
    """Every integer literal passed as an HTTP status in a module or package (AST)."""
    files: list[Path] = []
    for target in targets:
        if target.is_dir():
            files.extend(sorted(target.rglob("*.py")))
        elif target.exists():
            files.append(target)
    found: set[int] = set()
    for path in files:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            for keyword in node.keywords:
                if keyword.arg == "status" and _is_status(keyword.value):
                    found.add(int(keyword.value.value))
            # ``_error(DynamicAppErrorCode.X, 404, "...")`` — positional status.
            for argument in node.args[1:2]:
                if _is_status(argument):
                    found.add(int(argument.value))
    return found


def _is_status(node: ast.expr) -> bool:
    return (
        isinstance(node, ast.Constant)
        and isinstance(node.value, int)
        and not isinstance(node.value, bool)
        and 400 <= int(node.value) <= 599
    )


__all__ = [
    "API_PREFIX",
    "API_SRC",
    "WEB_SRC",
    "Call",
    "ServerErrors",
    "build_app",
    "content_fields",
    "content_vocabulary",
    "dynamic_app_fields",
    "dynamic_vocabulary",
    "operations",
    "path_parameters",
    "resource_fields",
    "resource_vocabulary",
    "route_matches",
    "server_errors",
]
