"""Content API — alias lifecycle states beyond creation.

``DELETE /content/aliases/{id}`` **disables** the permalink instead of deleting
it, because the row is the only owner of ``content_alias_bindings`` (which
``ON DELETE CASCADE`` would erase). This module pins the resulting state machine:

* a disabled alias stays inspectable inside its tenant/ACL (metadata + binding
  history) and stays listed with ``status="disabled"``;
* resolving it is ``403`` and binding a new version to it is ``409 CONFLICT``;
* ``PATCH status=active`` re-enables it and the retained binding serves bytes
  again — evidence is not rewritten by the disable;
* ``visibility=private`` is refused because ``content_aliases`` has no
  ``created_by`` column, so a creator-only rule could not be enforced;
* the company selector is mandatory: an empty selector must not fall back to
  platform-owned aliases, and a foreign tenant is ``404``.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from tests.conftest import requires_postgres
from tests.migration.content_api.fixtures import (
    CONTENT,
    Tenant,
    client,
    create_asset,
    tenant_a,
    tenant_b,
    upload_sealed_version,
)


def _create_alias(client: TestClient, tenant: Tenant, slug: str, **body):
    payload = {"slug": slug, "owner_company_id": tenant.company_id, **body}
    return client.post(f"{CONTENT}/aliases", headers=tenant.headers, json=payload)


def _bound_alias(
    client: TestClient, tenant: Tenant, *, slug: str, data: bytes
) -> tuple[dict, dict, dict]:
    asset = create_asset(client, tenant)
    sealed = upload_sealed_version(client, tenant, asset["id"], data)
    alias = _create_alias(client, tenant, slug).json()
    bound = client.post(
        f"{CONTENT}/aliases/{alias['id']}/bind",
        headers=tenant.headers,
        json={"asset_id": asset["id"], "blob_version_id": sealed["id"]},
    )
    assert bound.status_code == 200, bound.text
    return alias, asset, sealed


@requires_postgres
def test_disabled_alias_keeps_metadata_listing_and_history(
    client: TestClient, tenant_a: Tenant
) -> None:
    alias, _, _ = _bound_alias(client, tenant_a, slug="state.alias", data=b"served")

    disabled = client.delete(f"{CONTENT}/aliases/{alias['id']}", headers=tenant_a.headers)
    assert disabled.status_code == 200, disabled.text
    assert disabled.json()["status"] == "disabled"
    assert disabled.json()["retained_bindings"] == 1

    by_id = client.get(f"{CONTENT}/aliases/{alias['id']}", headers=tenant_a.headers)
    assert by_id.status_code == 200, by_id.text
    assert by_id.json()["status"] == "disabled"
    assert by_id.json()["slug"] == "state.alias"

    listed = client.get(
        f"{CONTENT}/aliases",
        headers=tenant_a.headers,
        params={"company_id": tenant_a.company_id},
    )
    assert listed.status_code == 200, listed.text
    row = [item for item in listed.json()["items"] if item["id"] == alias["id"]]
    assert row and row[0]["status"] == "disabled", "a disabled permalink stays discoverable"

    history = client.get(f"{CONTENT}/aliases/{alias['id']}/bindings", headers=tenant_a.headers)
    assert history.status_code == 200, history.text
    assert history.json()["alias"]["status"] == "disabled"
    assert len(history.json()["items"]) == 1

    resolved = client.get(f"{CONTENT}/aliases/state.alias/resolve", headers=tenant_a.headers)
    assert resolved.status_code == 403, resolved.text
    assert resolved.json()["code"] == "FORBIDDEN"


@requires_postgres
def test_binding_a_disabled_alias_is_a_conflict(client: TestClient, tenant_a: Tenant) -> None:
    alias, asset, sealed = _bound_alias(client, tenant_a, slug="no-rebind.alias", data=b"one")
    assert client.delete(
        f"{CONTENT}/aliases/{alias['id']}", headers=tenant_a.headers
    ).status_code == 200

    rejected = client.post(
        f"{CONTENT}/aliases/{alias['id']}/bind",
        headers=tenant_a.headers,
        json={"asset_id": asset["id"], "blob_version_id": sealed["id"]},
    )
    assert rejected.status_code == 409, rejected.text
    assert rejected.json()["code"] == "CONFLICT"


@requires_postgres
def test_re_enabling_an_alias_restores_the_retained_binding(
    client: TestClient, tenant_a: Tenant
) -> None:
    alias, _, sealed = _bound_alias(client, tenant_a, slug="revive.alias", data=b"revived")
    assert client.delete(
        f"{CONTENT}/aliases/{alias['id']}", headers=tenant_a.headers
    ).status_code == 200

    enabled = client.patch(
        f"{CONTENT}/aliases/{alias['id']}",
        headers=tenant_a.headers,
        json={"status": "active"},
    )
    assert enabled.status_code == 200, enabled.text
    assert enabled.json()["status"] == "active"

    resolved = client.get(
        f"{CONTENT}/aliases/revive.alias/resolve",
        headers=tenant_a.headers,
        params={"proxy": "true"},
    )
    assert resolved.status_code == 200, resolved.text
    assert resolved.content == b"revived"
    assert "X-Content-SHA256" in resolved.headers

    history = client.get(f"{CONTENT}/aliases/{alias['id']}/bindings", headers=tenant_a.headers)
    items = history.json()["items"]
    assert len(items) == 1, "re-enabling never rewrites binding history"
    assert items[0]["blob_version_id"] == sealed["id"]
    assert items[0]["superseded_at"] is None


@requires_postgres
def test_private_alias_visibility_is_refused(client: TestClient, tenant_a: Tenant) -> None:
    rejected = _create_alias(client, tenant_a, "private.alias", visibility="private")
    assert rejected.status_code == 422, rejected.text
    assert rejected.json()["code"] == "CONTENT_ALIAS_VISIBILITY_UNSUPPORTED"
    assert "created_by" in rejected.json()["detail"]

    created = _create_alias(client, tenant_a, "company.alias")
    assert created.status_code == 200, created.text
    patched = client.patch(
        f"{CONTENT}/aliases/{created.json()['id']}",
        headers=tenant_a.headers,
        json={"visibility": "private"},
    )
    assert patched.status_code == 422, patched.text
    assert patched.json()["code"] == "CONTENT_ALIAS_VISIBILITY_UNSUPPORTED"


@requires_postgres
def test_alias_list_selector_is_mandatory_and_tenant_scoped(
    client: TestClient, tenant_a: Tenant, tenant_b: Tenant
) -> None:
    assert _create_alias(client, tenant_a, "listed.alias").status_code == 200

    missing = client.get(f"{CONTENT}/aliases", headers=tenant_a.headers)
    assert missing.status_code == 422, missing.text
    assert missing.json()["code"] == "VALIDATION_ERROR"

    foreign = client.get(
        f"{CONTENT}/aliases",
        headers=tenant_a.headers,
        params={"company_id": tenant_b.company_id},
    )
    assert foreign.status_code == 404, foreign.text
    assert foreign.json()["code"] == "NOT_FOUND"


@requires_postgres
def test_asset_list_selector_is_mandatory(
    client: TestClient, tenant_a: Tenant
) -> None:
    response = client.get(f"{CONTENT}/assets", headers=tenant_a.headers)
    assert response.status_code == 422, response.text
    assert response.json()["code"] == "VALIDATION_ERROR"


@requires_postgres
def test_cross_tenant_alias_history_is_404(
    client: TestClient, tenant_a: Tenant, tenant_b: Tenant
) -> None:
    # ``_bound_alias`` returns (alias, asset, sealed version); this test only needs
    # the alias, but the helper's arity is what it is — unpacking two raised
    # ValueError before the request under test was even made.
    alias, _, _ = _bound_alias(client, tenant_a, slug="guarded.alias", data=b"bytes")

    history = client.get(f"{CONTENT}/aliases/{alias['id']}/bindings", headers=tenant_b.headers)
    assert history.status_code == 404, history.text
    assert history.json()["code"] == "NOT_FOUND"
