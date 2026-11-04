"""Payload parity for the dynamic-app surface.

The web normalizers (``apps/web/src/api/dynamic.ts``) are strict on purpose: a
missing or mistyped field becomes ``DynamicModulesProtocolError`` and the screen
refuses to render. That strictness is only useful if the field names match the
server, which is exactly what a mocked-``fetch`` vitest cannot tell us.

Every assertion below compares a *required* client field against the serializer
that produces the response body, so a rename on either side is caught here rather
than in the browser.
"""

from __future__ import annotations

import pytest

from server_contract import dynamic_app_fields, dynamic_vocabulary
from web_contract import DYNAMIC_CLIENT, interface_fields, read_field_aliases

#: Client interface -> the server serializer that must carry every required field.
ENTITY_SOURCES = {
    "DynamicModuleDefinitionSummary": "definition",
    "DynamicModuleDefinition": "definition",
    "DynamicModuleVersion": "version",
    "DynamicModuleInstance": "instance",
    "DynamicModuleGrant": "grant",
}

#: Client interface -> the normalizer whose wire aliases must cover it.
ENTITY_NORMALIZERS = {
    "DynamicModuleDefinitionSummary": "normalizeDefinitionSummary",
    "DynamicModuleDefinition": "normalizeDefinitionSummary",
    "DynamicModuleVersion": "normalizeVersion",
    "DynamicModuleInstance": "normalizeInstance",
    "DynamicModuleGrant": "normalizeGrant",
}

#: Fields the client derives from the *response envelope* or composes locally, so
#: they are not expected on the entity object itself.
DERIVED_FIELDS = frozenset()


def _server_fields(entity: str) -> set[str]:
    return dynamic_app_fields()[entity]


def test_client_reads_only_fields_the_serializer_emits() -> None:
    """Each normalized wire name must exist on the entity the server serializes.

    The check is on the *wire* names (``firstDefined(value.app_id, value.appId)``
    yields both), because either spelling is what actually arrives.
    """
    problems: list[str] = []
    for client_interface, normalizer in ENTITY_NORMALIZERS.items():
        aliases = read_field_aliases(DYNAMIC_CLIENT, normalizer)
        server = _server_fields(ENTITY_SOURCES[client_interface])
        for field, wire_names in aliases.items():
            if field in DERIVED_FIELDS:
                continue
            if not (set(wire_names) & server):
                problems.append(
                    f"{normalizer} reads {field!r} as {list(wire_names)}, "
                    f"but {ENTITY_SOURCES[client_interface]} emits only {sorted(server)}"
                )
    assert not problems, "\n".join(problems)


@pytest.mark.parametrize(
    "client_interface", sorted(ENTITY_SOURCES), ids=lambda name: name
)
def test_required_client_fields_are_served(client_interface: str) -> None:
    """A field the client requires (non-optional) cannot be absent from the response."""
    declared = interface_fields(DYNAMIC_CLIENT, client_interface)
    optional = _optional_fields(DYNAMIC_CLIENT, client_interface)
    required = {field for field in declared if field not in optional}
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
        f"{client_interface} requires {unserved}; the server serializes "
        f"{sorted(server)}"
    )


def _optional_fields(path, name: str) -> set[str]:
    """Fields declared with ``?`` in an interface (and so allowed to be absent)."""
    import re

    text = path.read_text(encoding="utf-8")
    block = re.search(rf"export interface {name}[^{{]*\{{(.*?)\n\}}", text, re.DOTALL)
    assert block, f"{name} is not declared in {path}"
    return set(re.findall(r"^\s*readonly\s+(\w+)\?", block.group(1), re.MULTILINE))


# ------------------------------------------------------------------- vocabulary


def test_definition_status_values_are_a_served_subset() -> None:
    """The status union the client accepts must contain only states the API emits.

    A client-only state is worse than an unrendered one: ``requireEnum`` would
    reject the real response for every row in that state.
    """
    served = dynamic_vocabulary()["lifecycle"]
    declared = _const_union(DYNAMIC_CLIENT, "DYNAMIC_MODULE_STATUSES")
    unknown = sorted(declared - served)
    assert not unknown, (
        f"dynamic.ts accepts status {unknown} that the dynamic-apps serializer never "
        f"emits (served: {sorted(served)})"
    )


def test_scope_and_decision_values_match_the_server() -> None:
    vocabulary = dynamic_vocabulary()
    assert _const_union(DYNAMIC_CLIENT, "DYNAMIC_MODULE_SCOPES") <= vocabulary["scope"]
    assert _const_union(DYNAMIC_CLIENT, "DYNAMIC_MODULE_KINDS") == {
        "declarative-app",
        "isolated-code-plugin",
    }, "manifest kind vocabulary is a client-side manifest contract; see CONTRACT.md §3"


def _const_union(path, name: str) -> set[str]:
    import re

    text = path.read_text(encoding="utf-8")
    block = re.search(rf"export const {name} = \[(.*?)\] as const", text, re.DOTALL)
    assert block, f"{name} is not declared in {path}"
    return set(re.findall(r'"([^"]+)"', block.group(1)))
