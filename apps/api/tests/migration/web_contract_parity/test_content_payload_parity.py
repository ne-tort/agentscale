"""Payload parity for the Content Service surface.

The content-ops adapter validates every field it reads before the panel can render
it, so the same rule applies as for dynamic modules: a field the client requires
must be emitted by ``application/content/schemas.py``, and a value the client
refuses to accept must never be produced by the server.
"""

from __future__ import annotations

import re

import pytest

from server_contract import content_fields, content_vocabulary, server_errors
from web_contract import (
    CONTENT_OPS_SERVICE,
    CONTENT_OPS_TYPES,
    interface_fields,
    status_keys,
)

#: Client interface -> the pydantic response model that must carry those fields.
MODEL_SOURCES = {
    "ContentAsset": "asset",
    "ContentAssetVersion": "version",
    "ContentAclEntry": "acl_entry",
}

#: The adapter reads these from the read-back payload rather than from a DTO field.
ENVELOPE_FIELDS = {
    "ContentAsset": frozenset(),
    "ContentAssetVersion": frozenset(),
    "ContentAclEntry": frozenset(),
}


@pytest.mark.parametrize("name", sorted(MODEL_SOURCES), ids=lambda name: name)
def test_client_fields_exist_on_the_response_model(name: str) -> None:
    declared = interface_fields(CONTENT_OPS_TYPES, name) - ENVELOPE_FIELDS[name]
    served, _ = content_fields()[MODEL_SOURCES[name]]
    unknown = sorted(declared - served)
    assert not unknown, (
        f"{name} declares {unknown} which {MODEL_SOURCES[name]} does not emit "
        f"(served: {sorted(served)})"
    )


def test_client_required_fields_are_on_the_model() -> None:
    """Fields the adapter demands (``safeAsset``/``safeVersion`` throw without them)."""
    _, asset_required = content_fields()["asset"]
    _, version_required = content_fields()["version"]
    assert {"id", "owner_company_id"} <= asset_required
    assert {"id", "asset_id", "version"} <= version_required


def test_acl_principal_kinds_accepted_by_the_client_cover_the_server() -> None:
    """The client must accept every principal kind the server can return.

    ``safeAclEntry`` throws ``INVALID_RESPONSE`` for a kind outside its list, and
    the panel calls it on the *read* path: an accepted-but-unlisted kind makes the
    whole access list unreadable instead of merely uneditable.
    """
    served = content_vocabulary()["principal_kind"]
    accepted = _principal_kinds(CONTENT_OPS_TYPES)
    missing = sorted(served - accepted)
    assert not missing, (
        f"content-ops rejects principal kinds {missing} that the ACL model emits "
        f"(accepted: {sorted(accepted)})"
    )


def test_acl_permissions_accepted_by_the_client_cover_the_server() -> None:
    served = content_vocabulary()["permission"]
    accepted = _union_members(CONTENT_OPS_TYPES, "AclPermission")
    assert served <= accepted, (served - accepted)


def test_client_error_statuses_are_producible_by_the_content_surface() -> None:
    """A status the adapter maps must be one the API can actually answer with.

    ``401`` is produced by the auth dependency in front of every route, so it is
    always reachable; anything else has to come from a declared error code.
    """
    reachable = set(server_errors().statuses) | {401}
    handled = set(status_keys(CONTENT_OPS_SERVICE, "safeError"))
    unreachable = sorted(handled - reachable)
    assert not unreachable, (
        f"{CONTENT_OPS_SERVICE.name} maps HTTP {unreachable}, which no content error code "
        f"or validation path can produce (reachable: {sorted(reachable)})"
    )


@pytest.mark.parametrize("status", sorted(status_keys(CONTENT_OPS_SERVICE, "safeError")))
def test_every_handled_status_has_a_message(status: int) -> None:
    messages = status_keys(CONTENT_OPS_SERVICE, "safeError")
    assert messages[status].strip(), f"HTTP {status} has an empty user-facing message"


def _principal_kinds(path) -> set[str]:
    return _union_members(path, "AclPrincipalKind")


def _union_members(path, name: str) -> set[str]:
    text = path.read_text(encoding="utf-8")
    match = re.search(rf"export type {name} =([^;]+);", text)
    assert match, f"{name} is not declared"
    return set(re.findall(r'"([^"]+)"', match.group(1)))
