"""Payload parity for the canonical Resource surface.

Two independent divergences are possible here and both are checked:

1. **Field names.** The resource API emits snake_case (``ResourceView.as_payload``),
   while the resources screens were written against camelCase fields *and*, in the
   workspace, against the snake_case wire names directly. ``features/resources/wire.ts``
   is the single adapter that maps one to the other; this module proves it covers
   every field the screens read and nothing the server cannot supply.
2. **Vocabulary.** ``ResourceLifecycle``, the plan status and the entry status are
   closed sets on the server. A client union that differs either rejects real rows
   or invents states.
"""

from __future__ import annotations

import re

import pytest

from .server_contract import operations, resource_fields, resource_vocabulary
from .web_contract import RESOURCE_TYPES, interface_fields, read_field_aliases
from .web_contract import WEB_SRC  # noqa: F401  (re-exported for readability below)

WIRE_MODULE = RESOURCE_TYPES.parent / "wire.ts"

#: Normalizer in ``wire.ts`` -> the server payload it must fully describe.
NORMALIZER_SOURCES = {
    "normalizeResourceRecord": "resource",
    "normalizeResourceVersion": "version",
    "normalizeMaterializationPlan": "plan",
}


def _server_payload(entity: str) -> set[str]:
    return resource_fields()[entity]


@pytest.mark.parametrize("normalizer", sorted(NORMALIZER_SOURCES))
def test_wire_normalizer_reads_only_served_fields(normalizer: str) -> None:
    aliases = read_field_aliases(WIRE_MODULE, normalizer)
    assert aliases, f"{normalizer} reads no field from the wire"
    server = _server_payload(NORMALIZER_SOURCES[normalizer])
    problems = [
        f"{field} as {list(names)}"
        for field, names in aliases.items()
        if not (set(names) & server)
    ]
    assert not problems, (
        f"{normalizer} reads {problems}, but the payload carries {sorted(server)}"
    )


@pytest.mark.parametrize(
    "client_interface",
    ["ResourceRecord", "ResourceVersion", "MaterializationPlan"],
    ids=lambda name: name,
)
def test_screen_dto_fields_are_produced_by_the_normalizer(client_interface: str) -> None:
    """Every field the workspace renders must be filled from a served field.

    DTO fields are camelCase; the normalizer output keys are camelCase as well, so
    the comparison is between the interface and the normalizer's returned keys.
    """
    produced = set(read_field_aliases(WIRE_MODULE, _normalizer_for(client_interface)))
    declared = interface_fields(RESOURCE_TYPES, client_interface)
    missing = sorted(declared - produced)
    assert not missing, (
        f"{client_interface} renders {missing} but {_normalizer_for(client_interface)} "
        "never produces them"
    )


def _normalizer_for(client_interface: str) -> str:
    return {
        "ResourceRecord": "normalizeResourceRecord",
        "ResourceVersion": "normalizeResourceVersion",
        "MaterializationPlan": "normalizeMaterializationPlan",
    }[client_interface]


# ------------------------------------------------------------------ vocabulary


def _union(name: str) -> set[str]:
    text = RESOURCE_TYPES.read_text(encoding="utf-8")
    match = re.search(rf"export type {name} =([^;]+);", text)
    assert match, f"{name} is not declared in {RESOURCE_TYPES}"
    return set(re.findall(r'"([^"]+)"', match.group(1)))


def test_resource_lifecycle_vocabulary_is_served_exactly() -> None:
    """The screen switches on these values, so the sets must be identical."""
    served = resource_vocabulary()["lifecycle"]
    declared = _union("ResourceLifecycle")
    assert declared == served, (
        f"ResourceLifecycle is {sorted(declared)}; the API emits {sorted(served)}"
    )


def test_plan_status_vocabulary_is_served_exactly() -> None:
    served = resource_vocabulary()["plan_status"]
    declared = _union("MaterializationStatus")
    assert declared == served, (
        f"MaterializationStatus is {sorted(declared)}; the plan API emits {sorted(served)}"
    )


def test_entry_status_vocabulary_is_served() -> None:
    served = resource_vocabulary()["plan_entry_status"]
    declared = _union("MaterializationEntryStatus")
    assert served <= declared, (
        f"plan entries can be {sorted(served)} but the screen accepts {sorted(declared)}"
    )


def test_version_and_scope_vocabulary_matches() -> None:
    vocabulary = resource_vocabulary()
    assert _union("ResourceVersionStatus") == vocabulary["version_status"], (
        f"ResourceVersionStatus is {sorted(_union('ResourceVersionStatus'))}; "
        f"the API emits {sorted(vocabulary['version_status'])}"
    )
    assert _union("ResourceScopeKind") >= vocabulary["owner_scope_kind"]
    assert _union("ResourceBindingMode") == vocabulary["binding_mode"]


def test_resource_kinds_match_the_creatable_set() -> None:
    assert _union("ResourceKind") == resource_vocabulary()["resource_kind"]


# ------------------------------------------------------- API capability coverage


#: ``ResourceService`` capability -> the operation that implements it.
CAPABILITY_ROUTES = {
    "loadWorkspace": ("GET", "/api/v1/companies/{company_id}/resources"),
    "readDiff": ("GET", "/api/v1/companies/{company_id}/resources/{resource_id}/versions"),
    "updateProfile": None,
    "createResource": ("POST", "/api/v1/companies/{company_id}/resources"),
    "createVersion": ("POST", "/api/v1/companies/{company_id}/resources/{resource_id}/versions"),
    "bindResource": ("POST", "/api/v1/companies/{company_id}/resources/{resource_id}/bindings"),
    "planMaterialization": (
        "POST",
        "/api/v1/companies/{company_id}/projects/{project_id}/resources/materialization/plans",
    ),
    "syncMaterialization": (
        "POST",
        "/api/v1/companies/{company_id}/projects/{project_id}/resources/materialization/applies",
    ),
}


@pytest.mark.parametrize("capability", sorted(CAPABILITY_ROUTES))
def test_blocked_capability_is_blocked_by_the_web_side_not_the_api(capability: str) -> None:
    """The delivered API covers every capability the blocked service refuses.

    ``createBlockedResourceService`` is the only wired implementation, so every
    capability is refused in the browser. That refusal is a *web wiring* gap: this
    check pins the API side of it, so the blocker text cannot silently drift into
    claiming an endpoint is missing. ``updateProfile`` has no canonical route at all
    and is the one capability whose blocker is genuinely on the API side.
    """
    route = CAPABILITY_ROUTES[capability]
    if route is None:
        assert capability == "updateProfile"
        assert not [path for _, path in operations() if "profile" in path.lower()], (
            "an agent-profile route now exists; the blocker for updateProfile must be "
            "re-derived instead of staying hardcoded"
        )
        return
    assert route in operations(), (
        f"{capability} is declared blocked but {route} is not registered on the "
        "resource router"
    )
