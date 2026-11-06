"""Content API — alias lifecycle and temporal bindings.

Covers: create/get/patch/disable, bind to a sealed version, NOT_READY for an
unsealed version, rebinding keeps history and supersedes the previous row,
resolve streams the bound bytes, cross-tenant alias/binding is 404, and the
disabled alias stops resolving while retaining evidence.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from tests.conftest import requires_postgres
from tests.migration.content_api.fixtures import (
    CONTENT,
    Tenant,
    begin_version,
    client,
    create_asset,
    tenant_a,
    tenant_b,
    upload_sealed_version,
)


def _create_alias(client: TestClient, tenant: Tenant, slug: str):
    return client.post(
        f"{CONTENT}/aliases",
        headers=tenant.headers,
        json={"slug": slug, "owner_company_id": tenant.company_id, "label": slug},
    )


@requires_postgres
def test_create_alias_normalises_the_slug(client: TestClient, tenant_a: Tenant) -> None:
    created = _create_alias(client, tenant_a, "Docs.ReadMe")
    assert created.status_code == 200, created.text
    body = created.json()
    assert body["slug"] == "docs.readme"
    assert body["owner_scope"] == "company"
    assert body["owner_company_id"] == tenant_a.company_id
    assert body["status"] == "active"


@requires_postgres
def test_alias_slug_conflict_is_409_for_the_same_tenant(
    client: TestClient, tenant_a: Tenant
) -> None:
    assert _create_alias(client, tenant_a, "taken.slug").status_code == 200
    again = _create_alias(client, tenant_a, "taken.slug")
    assert again.status_code == 409, again.text
    assert again.json()["code"] == "CONFLICT"


@requires_postgres
def test_alias_slug_reuse_across_tenants_does_not_leak_existence(
    client: TestClient, tenant_a: Tenant, tenant_b: Tenant
) -> None:
    assert _create_alias(client, tenant_a, "shared.slug").status_code == 200
    other = _create_alias(client, tenant_b, "shared.slug")
    assert other.status_code == 404, other.text
    assert other.json()["code"] == "NOT_FOUND"


@requires_postgres
def test_get_alias_by_id_and_slug(client: TestClient, tenant_a: Tenant) -> None:
    created = _create_alias(client, tenant_a, "lookup.slug").json()

    by_id = client.get(f"{CONTENT}/aliases/{created['id']}", headers=tenant_a.headers)
    assert by_id.status_code == 200, by_id.text
    by_slug = client.get(f"{CONTENT}/aliases/lookup.slug", headers=tenant_a.headers)
    assert by_slug.status_code == 200, by_slug.text
    assert by_slug.json()["id"] == created["id"]


@requires_postgres
def test_patch_alias_metadata_and_disable(client: TestClient, tenant_a: Tenant) -> None:
    created = _create_alias(client, tenant_a, "patch.slug").json()

    patched = client.patch(
        f"{CONTENT}/aliases/{created['id']}",
        headers=tenant_a.headers,
        json={"label": "New label", "status": "disabled", "metadata": {"owner": "docs"}},
    )
    assert patched.status_code == 200, patched.text
    body = patched.json()
    assert body["label"] == "New label"
    assert body["status"] == "disabled"
    assert body["metadata"] == {"owner": "docs"}


@requires_postgres
def test_bind_sealed_version_and_resolve_bytes(client: TestClient, tenant_a: Tenant) -> None:
    asset = create_asset(client, tenant_a, title="banner.png")
    sealed = upload_sealed_version(client, tenant_a, asset["id"], b"banner-bytes")
    alias = _create_alias(client, tenant_a, "banner.latest").json()

    bound = client.post(
        f"{CONTENT}/aliases/{alias['id']}/bind",
        headers=tenant_a.headers,
        json={"asset_id": asset["id"], "blob_version_id": sealed["id"]},
    )
    assert bound.status_code == 200, bound.text
    body = bound.json()
    assert body["blob_version_id"] == sealed["id"]
    assert body["superseded_at"] is None
    assert body["bound_by"] == tenant_a.employee_id

    resolved = client.get(
        f"{CONTENT}/aliases/banner.latest/resolve",
        headers=tenant_a.headers,
        params={"proxy": "true"},
    )
    assert resolved.status_code == 200, resolved.text
    assert resolved.content == b"banner-bytes"
    assert "banner.png" in resolved.headers["Content-Disposition"]


@requires_postgres
def test_bind_without_a_version_uses_the_latest_sealed_version(
    client: TestClient, tenant_a: Tenant
) -> None:
    asset = create_asset(client, tenant_a)
    upload_sealed_version(client, tenant_a, asset["id"], b"v1")
    latest = upload_sealed_version(client, tenant_a, asset["id"], b"v2")
    alias = _create_alias(client, tenant_a, "latest.only").json()

    bound = client.post(
        f"{CONTENT}/aliases/{alias['id']}/bind",
        headers=tenant_a.headers,
        json={"asset_id": asset["id"]},
    )
    assert bound.status_code == 200, bound.text
    assert bound.json()["blob_version_id"] == latest["id"]


@requires_postgres
def test_bind_unsealed_version_is_not_ready(client: TestClient, tenant_a: Tenant) -> None:
    asset = create_asset(client, tenant_a)
    begun = begin_version(client, tenant_a, asset["id"])
    alias = _create_alias(client, tenant_a, "unsealed.alias").json()

    rejected = client.post(
        f"{CONTENT}/aliases/{alias['id']}/bind",
        headers=tenant_a.headers,
        json={"asset_id": asset["id"], "blob_version_id": begun["version"]["id"]},
    )
    assert rejected.status_code == 422, rejected.text
    assert rejected.json()["code"] == "NOT_READY"


@requires_postgres
def test_bind_asset_without_any_sealed_version_is_not_ready(
    client: TestClient, tenant_a: Tenant
) -> None:
    asset = create_asset(client, tenant_a)
    begin_version(client, tenant_a, asset["id"])
    alias = _create_alias(client, tenant_a, "empty.alias").json()

    rejected = client.post(
        f"{CONTENT}/aliases/{alias['id']}/bind",
        headers=tenant_a.headers,
        json={"asset_id": asset["id"]},
    )
    assert rejected.status_code == 422, rejected.text
    assert rejected.json()["code"] == "NOT_READY"


@requires_postgres
def test_rebinding_keeps_history_and_supersedes_the_previous_binding(
    client: TestClient, tenant_a: Tenant
) -> None:
    asset = create_asset(client, tenant_a)
    first = upload_sealed_version(client, tenant_a, asset["id"], b"one")
    second = upload_sealed_version(client, tenant_a, asset["id"], b"two")
    alias = _create_alias(client, tenant_a, "evolving.alias").json()

    client.post(
        f"{CONTENT}/aliases/{alias['id']}/bind",
        headers=tenant_a.headers,
        json={"asset_id": asset["id"], "blob_version_id": first["id"]},
    )
    rebound = client.post(
        f"{CONTENT}/aliases/{alias['id']}/bind",
        headers=tenant_a.headers,
        json={"asset_id": asset["id"], "blob_version_id": second["id"]},
    )
    assert rebound.status_code == 200, rebound.text

    history = client.get(f"{CONTENT}/aliases/{alias['id']}/bindings", headers=tenant_a.headers)
    assert history.status_code == 200, history.text
    items = history.json()["items"]
    assert len(items) == 2, "superseded bindings are evidence and must be retained"
    current = [item for item in items if item["superseded_at"] is None]
    assert len(current) == 1
    assert current[0]["blob_version_id"] == second["id"]
    superseded = [item for item in items if item["superseded_at"]]
    assert superseded[0]["blob_version_id"] == first["id"]

    resolved = client.get(
        f"{CONTENT}/aliases/evolving.alias/resolve",
        headers=tenant_a.headers,
        params={"proxy": "true"},
    )
    assert resolved.content == b"two"


@requires_postgres
def test_rebinding_the_same_version_is_a_replay(client: TestClient, tenant_a: Tenant) -> None:
    asset = create_asset(client, tenant_a)
    sealed = upload_sealed_version(client, tenant_a, asset["id"], b"same")
    alias = _create_alias(client, tenant_a, "stable.alias").json()

    first = client.post(
        f"{CONTENT}/aliases/{alias['id']}/bind",
        headers=tenant_a.headers,
        json={"asset_id": asset["id"], "blob_version_id": sealed["id"]},
    ).json()
    second = client.post(
        f"{CONTENT}/aliases/{alias['id']}/bind",
        headers=tenant_a.headers,
        json={"asset_id": asset["id"], "blob_version_id": sealed["id"]},
    ).json()
    assert second["id"] == first["id"]
    assert second["replayed"] is True

    history = client.get(f"{CONTENT}/aliases/{alias['id']}/bindings", headers=tenant_a.headers)
    assert len(history.json()["items"]) == 1


@requires_postgres
def test_unbind_stops_resolution_but_keeps_history(
    client: TestClient, tenant_a: Tenant
) -> None:
    asset = create_asset(client, tenant_a)
    sealed = upload_sealed_version(client, tenant_a, asset["id"], b"x")
    alias = _create_alias(client, tenant_a, "unbind.alias").json()
    client.post(
        f"{CONTENT}/aliases/{alias['id']}/bind",
        headers=tenant_a.headers,
        json={"asset_id": asset["id"], "blob_version_id": sealed["id"]},
    )

    unbound = client.delete(f"{CONTENT}/aliases/{alias['id']}/bind", headers=tenant_a.headers)
    assert unbound.status_code == 200, unbound.text
    assert unbound.json()["unbound"] is True

    again = client.delete(f"{CONTENT}/aliases/{alias['id']}/bind", headers=tenant_a.headers)
    assert again.status_code == 200, again.text
    assert again.json()["unbound"] is False

    resolved = client.get(
        f"{CONTENT}/aliases/unbind.alias/resolve",
        headers=tenant_a.headers,
        params={"proxy": "true"},
    )
    assert resolved.status_code == 404, resolved.text
    assert resolved.json()["code"] == "NOT_READY"

    history = client.get(f"{CONTENT}/aliases/{alias['id']}/bindings", headers=tenant_a.headers)
    assert len(history.json()["items"]) == 1, "unbinding must not delete evidence"


@requires_postgres
def test_cross_tenant_binding_is_rejected(
    client: TestClient, tenant_a: Tenant, tenant_b: Tenant
) -> None:
    asset_b = create_asset(client, tenant_b)
    sealed_b = upload_sealed_version(client, tenant_b, asset_b["id"], b"b-bytes")
    alias_a = _create_alias(client, tenant_a, "cross.alias").json()

    rejected = client.post(
        f"{CONTENT}/aliases/{alias_a['id']}/bind",
        headers=tenant_a.headers,
        json={"asset_id": asset_b["id"], "blob_version_id": sealed_b["id"]},
    )
    assert rejected.status_code == 404, (
        f"a foreign asset must not be discoverable: {rejected.text}"
    )


@requires_postgres
def test_cross_tenant_alias_mutation_is_404(
    client: TestClient, tenant_a: Tenant, tenant_b: Tenant
) -> None:
    alias = _create_alias(client, tenant_a, "shielded.alias").json()

    read = client.get(f"{CONTENT}/aliases/{alias['id']}", headers=tenant_b.headers)
    assert read.status_code == 404, read.text

    patched = client.patch(
        f"{CONTENT}/aliases/{alias['id']}", headers=tenant_b.headers, json={"label": "hijack"}
    )
    assert patched.status_code == 404, patched.text

    deleted = client.delete(f"{CONTENT}/aliases/{alias['id']}", headers=tenant_b.headers)
    assert deleted.status_code == 404, deleted.text

    bind = client.post(
        f"{CONTENT}/aliases/{alias['id']}/bind",
        headers=tenant_b.headers,
        json={"asset_id": "cast_none"},
    )
    assert bind.status_code == 404, bind.text

    resolved = client.get(
        f"{CONTENT}/aliases/shielded.alias/resolve", headers=tenant_b.headers
    )
    assert resolved.status_code == 404, resolved.text


@requires_postgres
def test_delete_disables_the_alias_and_retains_evidence(
    client: TestClient, tenant_a: Tenant
) -> None:
    asset = create_asset(client, tenant_a)
    sealed = upload_sealed_version(client, tenant_a, asset["id"], b"keep")
    alias = _create_alias(client, tenant_a, "archived.alias").json()
    client.post(
        f"{CONTENT}/aliases/{alias['id']}/bind",
        headers=tenant_a.headers,
        json={"asset_id": asset["id"], "blob_version_id": sealed["id"]},
    )

    deleted = client.delete(f"{CONTENT}/aliases/{alias['id']}", headers=tenant_a.headers)
    assert deleted.status_code == 200, deleted.text
    body = deleted.json()
    assert body["deleted"] is True
    assert body["retained_evidence"] is True
    assert body["status"] == "disabled"

    history = client.get(f"{CONTENT}/aliases/{alias['id']}/bindings", headers=tenant_a.headers)
    assert len(history.json()["items"]) == 1, "disabling must not cascade away the history"

    resolved = client.get(
        f"{CONTENT}/aliases/archived.alias/resolve", headers=tenant_a.headers
    )
    assert resolved.status_code == 403, resolved.text
    assert resolved.json()["code"] == "FORBIDDEN"
