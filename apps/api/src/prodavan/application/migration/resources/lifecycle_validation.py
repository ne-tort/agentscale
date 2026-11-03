"""Pure payload and identity validation for MIG-RES-01."""
from __future__ import annotations

from collections.abc import Mapping as MappingABC
from typing import Mapping

from .lifecycle_contracts import (
    Binding,
    CompatibilityCandidate,
    Resource,
    ResourceKind,
    ResourceLifecycle,
    ResourceVersion,
    ScopeKind,
    canonical_digest,
    is_secret_reference,
    normalize_asset_digest,
    reject_secret_material,
    safe_workspace_path,
)


def asset_from_payload(payload: MappingABC[str, object]):
    raw = payload.get("asset_ref", payload.get("file_ref", payload.get("asset")))
    if raw is None and all(key in payload for key in ("asset_id", "version_id", "storage_key", "filename")):
        raw = payload
    if raw is None:
        return None
    from .lifecycle_contracts import AssetReference
    if not isinstance(raw, MappingABC):
        raise ValueError("asset reference must be an object")
    return AssetReference.from_mapping(raw)


def prompt_body(payload: MappingABC[str, object]) -> str | None:
    for key in ("body_md", "body", "content"):
        value = payload.get(key)
        if isinstance(value, str):
            return value
    return None


def _mcp_manifest(payload: MappingABC[str, object]):
    for key in ("manifest", "mcp_manifest", "mcp"):
        value = payload.get(key)
        if isinstance(value, MappingABC):
            return value
    return payload if "mcpServers" in payload else None


def mcp_manifest_error(payload: MappingABC[str, object]) -> str | None:
    """Accept only bounded MCP command/url manifests with reference-only secrets."""
    manifest = _mcp_manifest(payload)
    if manifest is None:
        return "unsupported_mcp_manifest"
    servers = manifest.get("mcpServers")
    if not isinstance(servers, MappingABC) or not servers:
        return "unsupported_mcp_manifest"
    allowed = {"command", "args", "url", "transport", "env", "headers", "cwd", "tools"}
    for name, config in servers.items():
        if not isinstance(name, str) or not name.strip() or not isinstance(config, MappingABC):
            return "unsupported_mcp_manifest"
        if any(str(key) not in allowed for key in config):
            return "unsupported_mcp_manifest"
        command, url = config.get("command"), config.get("url")
        if (command is None) == (url is None):
            return "unsupported_mcp_manifest"
        if command is not None and (not isinstance(command, str) or not command.strip()):
            return "unsupported_mcp_manifest"
        if url is not None and (not isinstance(url, str) or not url.startswith(("http://", "https://"))):
            return "unsupported_mcp_manifest"
        expected = "stdio" if command is not None else "http"
        transport = config.get("transport")
        if transport is not None and transport not in {"stdio", "http", "sse"}:
            return "unsupported_mcp_manifest"
        if transport is not None and transport != expected and not (expected == "http" and transport == "sse"):
            return "unsupported_mcp_manifest"
        args = config.get("args")
        if args is not None and (not isinstance(args, (list, tuple)) or any(not isinstance(item, str) for item in args)):
            return "unsupported_mcp_manifest"
        env = config.get("env")
        if env is not None:
            if not isinstance(env, MappingABC):
                return "unsupported_mcp_manifest"
            try:
                reject_secret_material(env)
            except ValueError:
                return "unsupported_mcp_manifest"
        headers = config.get("headers")
        if headers is not None:
            if not isinstance(headers, MappingABC):
                return "unsupported_mcp_manifest"
            for key, value in headers.items():
                if str(key).lower() in {"authorization", "proxy-authorization", "x-api-key"} and not is_secret_reference(value):
                    return "unsupported_mcp_manifest"
    return None


def validate_resource_payload(kind: ResourceKind, payload: Mapping[str, object]) -> tuple[str, ...]:
    """Return stable reason codes; no storage or network reads are performed."""
    if not isinstance(payload, MappingABC):
        return ("payload_not_object",)
    try:
        reject_secret_material(payload)
    except ValueError:
        return ("secret_material_forbidden",)
    errors: list[str] = []
    path = payload.get("path") or payload.get("target_path")
    if path is not None and not safe_workspace_path(path):
        errors.append("unsafe_target_path")
    try:
        asset = asset_from_payload(payload)
    except ValueError:
        asset = None
        errors.append("immutable_asset_reference_invalid")
    declared = payload.get("asset_digest")
    if declared is not None:
        try:
            normalized = normalize_asset_digest(declared)
        except ValueError:
            errors.append("immutable_asset_digest_invalid")
        else:
            if asset is not None and asset.digest != normalized:
                errors.append("asset_digest_conflict")
    if kind is ResourceKind.PROMPT:
        has_body = prompt_body(payload) is not None
        has_fragments = isinstance(payload.get("prompt_fragments"), (list, tuple))
        has_files = isinstance(payload.get("files"), (MappingABC, list, tuple))
        if not has_body and not has_fragments and not has_files and asset is None:
            errors.append("prompt_content_missing")
    elif kind is ResourceKind.FILE:
        if asset is None:
            errors.append("immutable_asset_missing")
    elif kind is ResourceKind.MCP:
        if asset is None:
            errors.append("immutable_asset_missing")
        manifest_error = mcp_manifest_error(payload)
        if manifest_error:
            errors.append(manifest_error)
    return tuple(dict.fromkeys(errors))


def _canonical_scope(scope) -> dict[str, object]:
    return {"kind": scope.kind.value, "id": scope.scope_id, "tenant": scope.tenant_id}


def _canonical_version(version: ResourceVersion) -> dict[str, object]:
    return {
        "id": version.version_id,
        "resource_id": version.resource_id,
        "sequence": version.sequence,
        "schema": version.schema_version,
        "status": version.status.value,
        "content_digest": version.content_digest,
        "asset_digest": version.asset_digest,
        "payload": version.payload,
        "secret_references": [
            {"provider": item.provider, "locator": item.locator, "version": item.version}
            for item in version.secret_references
        ],
    }


def _canonical_binding(binding: Binding) -> dict[str, object]:
    return {
        "id": binding.binding_id,
        "resource_id": binding.resource_id,
        "version_id": binding.version_id,
        "owner_scope": _canonical_scope(binding.scope),
        "target_scope": _canonical_scope(binding.effective_target_scope),
        "state": binding.state.value,
        "session_id": binding.session_id,
        "project_id": binding.project_id,
        "project_ids": binding.project_ids,
        "selection_policy": binding.selection_policy.value if binding.selection_policy else None,
        "binding_kind": binding.binding_kind.value,
        "profile_id": binding.profile_id,
        "priority": binding.priority,
        "generation": binding.generation,
    }


def candidate_canonical_digest(candidate: CompatibilityCandidate) -> str:
    """Hash full source envelope, all immutable versions and binding policy."""
    return canonical_digest({
        "source": candidate.source_envelope,
        "source_identity": {
            "kind": candidate.source_kind,
            "id": candidate.source_id,
            "schema": candidate.schema_version,
        },
        "resource": {
            "id": candidate.resource.resource_id,
            "kind": candidate.resource.kind.value,
            "scope": _canonical_scope(candidate.resource.scope),
            "lifecycle": candidate.resource.lifecycle.value,
            "metadata": candidate.resource.metadata,
            "active_version": candidate.resource.active_version,
            "versions": [_canonical_version(item) for item in candidate.resource.versions],
        },
        "binding": _canonical_binding(candidate.binding),
    })


def candidate_asset_digests(candidate: CompatibilityCandidate) -> tuple[str, ...]:
    return tuple(item for item in (version.asset_digest for version in candidate.resource.versions) if item is not None)


def validate_candidate(candidate: CompatibilityCandidate) -> tuple[str, ...]:
    """Classify malformed/cross-scope rows before an import port is called."""
    resource, binding = candidate.resource, candidate.binding
    errors: list[str] = []
    version = resource.version_by_id(binding.version_id)
    if binding.resource_id != resource.resource_id:
        errors.append("binding_resource_mismatch")
    if binding.scope != resource.scope:
        errors.append("binding_owner_scope_mismatch")
    if version is None:
        errors.append("binding_version_missing")
    elif resource.kind in {ResourceKind.PROMPT, ResourceKind.FILE, ResourceKind.MCP}:
        errors.extend(validate_resource_payload(resource.kind, version.payload))
        if version.status.value != "published":
            errors.append("version_not_published")
        if resource.active_version is not None and resource.active_version != version.sequence:
            errors.append("active_version_mismatch")
    errors.extend(binding.policy_errors())
    if resource.lifecycle is ResourceLifecycle.ARCHIVED:
        errors.append("resource_archived")
    if resource.lifecycle is ResourceLifecycle.QUARANTINED:
        errors.append("resource_already_quarantined")
    if binding.state.value == "disabled":
        errors.append("binding_disabled")
    asserted_tenant = candidate.source_envelope.get("tenant_id")
    if asserted_tenant is not None and asserted_tenant != resource.scope.tenant_id:
        errors.append("cross_tenant_source_assertion")
    asserted_project = candidate.source_envelope.get("project_id")
    if asserted_project is not None:
        target = binding.effective_target_scope
        if target.kind is not ScopeKind.PROJECT or target.scope_id != asserted_project:
            errors.append("cross_project_source_assertion")
    return tuple(dict.fromkeys(errors))


__all__ = [
    "asset_from_payload", "candidate_asset_digests", "candidate_canonical_digest",
    "mcp_manifest_error", "prompt_body", "validate_candidate", "validate_resource_payload",
]
