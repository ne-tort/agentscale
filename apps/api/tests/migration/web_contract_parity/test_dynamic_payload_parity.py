"""Payload parity for the dynamic-app surface.

The web normalizers (``apps/web/src/api/dynamic.ts``) validate every field at the
boundary: a missing or mistyped field becomes ``DynamicModulesProtocolError`` and the
screen refuses to render rather than showing a guessed value. That strictness only
helps if the names match the server, which a mocked-``fetch`` vitest cannot check.

Three properties are enforced here, in the order the client breaks:

1. **Nothing the client requires is absent.** A required field the serializer never
   emits means every response is rejected.
2. **Every wire name the client reads is served**, or listed in
   ``UNSERVED_BY_DESIGN`` with the reason — and such a field must then be optional.
3. **Every value the server can emit is accepted.** ``requireEnum`` throws on an
   unknown member, and the lifecycle vocabulary comes from ``api_lifecycle()``, so
   that direction is checked explicitly.
"""

from __future__ import annotations

import re

import pytest

from .server_contract import dynamic_app_fields, dynamic_vocabulary
from .web_contract import DYNAMIC_CLIENT, interface_fields, optional_fields, read_field_aliases

#: Client interface -> the server serializer that must carry its required fields.
ENTITY_SOURCES = {
    "DynamicModuleDefinitionSummary": "definition",
    "DynamicModuleDefinition": "definition",
    "DynamicModuleVersion": "version",
    "DynamicModuleInstance": "instance",
    "DynamicModuleGrant": "grant",
}

#: Client interface -> the normalizer that reads it off the wire.
ENTITY_NORMALIZERS = {
    "DynamicModuleDefinitionSummary": "normalizeDefinitionSummary",
    "DynamicModuleDefinition": "normalizeDefinition",
    "DynamicModuleVersion": "normalizeVersion",
    "DynamicModuleInstance": "normalizeInstance",
    "DynamicModuleGrant": "normalizeGrant",
}

#: Wire names the client reads that the API does not send, each with the reason it is
#: allowed to exist. Every entry is a deliberate, documented gap: the field must also
#: be optional, which ``test_unserved_client_fields_are_optional`` proves.
UNSERVED_BY_DESIGN = {
    # The definition row has no `kind` column, so the serializer cannot send one. The
    # screens show "kind not reported" instead of inventing a default.
    "kind": "no kind column on dynamic_app_definitions",
    # The definition/version views carry no manifest body; only its digest is emitted.
    "manifest": "the version view emits artifact_digest, not the manifest",
    # A grant row lists its capabilities as a set; the version of each accepted
    # capability belongs to the manifest, not to the decision row.
    "capabilityVersion": "not echoed by the grant view",
    # `scope` itself is served (`{kind, id}`); the two flat ids are what the client
    # derives from it in `normalizeScope`.
    "cabinetId": "derived from scope.id when scope.kind is cabinet",
    "projectId": "derived from scope.id when scope.kind is project",
    # Nullable/optional view members the client tolerates as absent.
    "description": "nullable in the definition view",
    "notes": "never emitted; the version view has no notes column",
    "createdBy": "the definition/version views do not emit an actor",
    "ownerCabinetId": "the definition scope is the tenant, not a cabinet",
    "ownerCompanyId": "the definition scope is the tenant, not a company",
    "routes": "the definition view has no route table",
    "latestVersion": "the view emits current_version_id, not a semver",
    "versionCount": "the client counts the versions array instead",
    "requestedCapabilityCount": "the view emits requested_capabilities as a list",
    "archivedAt": "archived is a lifecycle value, not a timestamp",
    "installedBy": "the instance view has no actor column",
    "grantedBy": "the grant decision rows carry no actor in the view",
    "revokedAt": "revocation is a denied decision, not a timestamp",
    "reason": "per-capability reason is not echoed by the grant view",
    "instanceId": "a grant row is issued for a scope and version, not for one instance",
    # The instance view identifies the installed version by id; a *version* DTO's own
    # semver is served (as `manifest_version`), so this gap is instance-specific.
    "version": "the instance view identifies the version by id only",
}


def _server_fields(entity: str) -> set[str]:
    return dynamic_app_fields()[entity]


def test_client_reads_only_served_or_explicitly_unserved_fields() -> None:
    """Every wire name the client reads is either served or listed with a reason."""
    problems: list[str] = []
    for client_interface, normalizer in ENTITY_NORMALIZERS.items():
        aliases = read_field_aliases(DYNAMIC_CLIENT, normalizer)
        server = _server_fields(ENTITY_SOURCES[client_interface])
        for field, wire_names in aliases.items():
            if set(wire_names) & server:
                continue
            if field in UNSERVED_BY_DESIGN:
                continue
            problems.append(
                f"{normalizer} reads {field!r} as {list(wire_names)}, "
                f"but {ENTITY_SOURCES[client_interface]} emits only {sorted(server)}"
            )
    assert not problems, "\n".join(problems)


def test_unserved_client_fields_are_optional() -> None:
    """An unserved field must be optional, or every response would be rejected.

    This is what keeps ``UNSERVED_BY_DESIGN`` honest: it can excuse a field that is
    genuinely absent, but not a *required* one — that is the case where a screen fails
    closed for every row. The excuse only applies where the field really is unserved:
    ``version`` is absent from an instance but served (as ``manifest_version``) on a
    version, so the version DTO keeps it required.
    """
    required_gaps: list[str] = []
    for client_interface in ENTITY_SOURCES:
        optional = optional_fields(DYNAMIC_CLIENT, client_interface)
        aliases = read_field_aliases(DYNAMIC_CLIENT, ENTITY_NORMALIZERS[client_interface])
        server = _server_fields(ENTITY_SOURCES[client_interface])
        for field in UNSERVED_BY_DESIGN:
            if field not in interface_fields(DYNAMIC_CLIENT, client_interface):
                continue
            if field in optional:
                continue
            if set(aliases.get(field, ())) & server:
                continue
            required_gaps.append(f"{client_interface}.{field}")
    assert not required_gaps, (
        "these fields are declared required but the API cannot serve them: "
        f"{sorted(required_gaps)}"
    )


@pytest.mark.parametrize("client_interface", sorted(ENTITY_SOURCES), ids=lambda name: name)
def test_required_client_fields_are_served(client_interface: str) -> None:
    required = interface_fields(DYNAMIC_CLIENT, client_interface) - optional_fields(
        DYNAMIC_CLIENT, client_interface
    )
    aliases = read_field_aliases(DYNAMIC_CLIENT, ENTITY_NORMALIZERS[client_interface])
    server = _server_fields(ENTITY_SOURCES[client_interface])

    unmapped = sorted(field for field in required if field not in aliases)
    assert not unmapped, (
        f"{client_interface} requires {unmapped} but the normalizer never reads them"
    )
    unserved = sorted(
        f"{field} ({list(aliases[field])})"
        for field in required
        if not (set(aliases[field]) & server)
    )
    assert not unserved, (
        f"{client_interface} requires {unserved}; the server serializes {sorted(server)}"
    )


# ------------------------------------------------------------------- vocabulary


def test_server_lifecycle_values_are_all_accepted_by_the_client() -> None:
    """The client must not reject a status the API can send.

    ``api_lifecycle`` maps every persisted status, including ``revoked`` — a row the
    CRUD surface never writes but can still read. ``requireEnum`` throws on a value
    outside the union, which would fail the whole list response for one such row.
    """
    served = dynamic_vocabulary()["lifecycle"]
    declared = _const_union(DYNAMIC_CLIENT, "DYNAMIC_MODULE_STATUSES")
    rejected = sorted(served - declared)
    assert not rejected, (
        f"dynamic.ts rejects lifecycle {rejected} that the dynamic-apps serializer can "
        f"emit (accepted: {sorted(declared)})"
    )


def test_client_lifecycle_union_is_bounded_by_the_registry_contract() -> None:
    """Extra client states must come from the documented registry lifecycle (§7.1).

    ``validated``/``approved``/``disabled`` are registry states in
    ``dynamic-ui-implementation-contract.md`` §7.1 that the CRUD surface does not reach
    yet. Accepting them is harmless; inventing a state that appears nowhere in the
    contract is not, so that is what this forbids.
    """
    contract_states = {
        "draft",
        "validated",
        "approved",
        "published",
        "disabled",
        "archived",
        "rejected",
        "rolled_back",
        "revoked",
    }
    declared = _const_union(DYNAMIC_CLIENT, "DYNAMIC_MODULE_STATUSES")
    assert declared <= contract_states, sorted(declared - contract_states)
    assert dynamic_vocabulary()["lifecycle"] <= declared


def test_scope_values_match_the_server() -> None:
    assert _const_union(DYNAMIC_CLIENT, "DYNAMIC_MODULE_SCOPES") == dynamic_vocabulary()["scope"]


def _const_union(path, name: str) -> set[str]:
    text = path.read_text(encoding="utf-8")
    block = re.search(rf"export const {name} = \[(.*?)\] as const", text, re.DOTALL)
    assert block, f"{name} is not declared in {path}"
    return set(re.findall(r'"([^"]+)"', block.group(1)))
