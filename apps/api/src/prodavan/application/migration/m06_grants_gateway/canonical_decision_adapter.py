"""Read-only projection of the canonical grant check for shadow parity."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Mapping

from prodavan.application.canonical_grants.contracts import CanonicalGrant, CanonicalGrantReason
from prodavan.application.canonical_grants.ports import CanonicalGrantReadPort

from .adapter import CanonicalShadowDecisionPort, ResolvedGrantIdentity, ShadowDecision, binding_digest


class CanonicalGrantShadowAdapter(CanonicalShadowDecisionPort):
    """Read and evaluate canonical grant rows; never authorizes or mutates a request."""

    def __init__(self, *, grants: CanonicalGrantReadPort) -> None:
        self._grants = grants

    async def decide_shadow(self, *, identity: ResolvedGrantIdentity, capability: str,
                            operation: str, resource: str, domain: str | None,
                            constraints: Mapping[str, Any], now: datetime) -> ShadowDecision:
        binding = binding_digest(identity)
        if capability not in identity.requested_capabilities:
            return ShadowDecision(False, CanonicalGrantReason.CAPABILITY_NOT_REQUESTED.value, binding)
        try:
            candidates = await self._grants.list_grants(
                tenant_id=identity.tenant_id, app_id=identity.app_id,
                app_version_id=identity.app_version_id,
                plugin_instance_id=identity.plugin_instance_id,
            )
        except Exception as exc:
            raise RuntimeError("canonical_grant_read_failed") from exc
        if any(not _representable(grant) for grant in candidates):
            return ShadowDecision(False, CanonicalGrantReason.UNREPRESENTABLE_GRANT.value, binding)
        exact: list[CanonicalGrant] = []
        for grant in candidates:
            if (grant.tenant_id == identity.tenant_id
                    and grant.app_definition_id == identity.app_definition_id
                    and grant.app_id == identity.app_id
                    and grant.app_version_id == identity.app_version_id
                    and grant.app_artifact_digest == identity.artifact_digest
                    and grant.scope_kind == identity.scope_kind
                    and grant.scope_id == identity.scope_id
                    and grant.plugin_instance_id == identity.plugin_instance_id
                    and grant.capability_key == capability):
                exact.append(grant)
        if not exact:
            return ShadowDecision(False, "grant_missing", binding)
        failures: list[tuple[str, str]] = []
        for grant in sorted(exact, key=lambda item: item.grant_id):
            reason = grant.check(
                operation=operation, resource=resource, domain=domain or "",
                constraints=constraints, subject_id=identity.subject_id,
                session_id=identity.session_id, now=now,
            )
            if reason is None:
                return ShadowDecision(True, "allowed", binding)
            failures.append((grant.grant_id, reason.value))
        failures.sort()
        return ShadowDecision(False, failures[0][1], binding)


def _representable(grant: CanonicalGrant) -> bool:
    values = (*grant.operations, *grant.resources, *grant.domains)
    return bool(
        grant.grant_id.strip() and grant.capability_key.strip() and values
        and all(value.strip() and not any(token in value for token in ("*", "?", "[", "]"))
                for value in values)
        and _json_constraint_shape(grant.constraints)
    )


def _json_constraint_shape(value: object) -> bool:
    if value is None or isinstance(value, (bool, int, float)):
        return True
    if isinstance(value, str):
        return not any(token in value for token in ("*", "?", "[", "]"))
    if isinstance(value, Mapping):
        return all(isinstance(key, str) and key.strip() and _json_constraint_shape(item)
                   for key, item in value.items())
    if isinstance(value, (tuple, list, frozenset)):
        return all(_json_constraint_shape(item) for item in value)
    return False


__all__ = ["CanonicalGrantShadowAdapter"]