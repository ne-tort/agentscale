"""Content API — archive refusal, retained evidence and destructive purge.

``DELETE /content/assets/{id}`` defaults to an **archive**, which cannot be
persisted because ``content_assets`` has no ``status``/``archived_at`` column.
The route therefore fails closed with ``409 CONTENT_ARCHIVE_UNAVAILABLE`` and
returns the evidence it refused to destroy:

* ``GET /content/assets/{id}/dependents`` — live/historical alias bindings,
  entity links and the version count, behind the same ``admin`` gate;
* platform-admin ``?purge=true`` — refuses an asset with alias binding history
  (the FK cascade would erase resolve history) and refuses entity links without
  ``cascade=true``.

Covers: archive refusal shape, evidence report, tenant isolation of both,
tenant admin cannot purge, purge blocked by binding history, purge requires
``cascade`` for links, and a clean purge that removes the asset and its blobs.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from tests.conftest import requires_postgres
from tests.migration.content_api.fixtures import (
    CONTENT,
    Tenant,
    client,
    create_asset,
    platform_headers,
    tenant_a,
    tenant_b,
    upload_sealed_version,
)


def _dependents(client: TestClient, tenant: Tenant, asset_id: str):
    return client.get(f"{CONTENT}/assets/{asset_id}/dependents", headers=tenant.headers)


@requires_postgres
def test_archive_fails_closed_and_keeps_the_asset(
    client: TestClient, tenant_a: Tenant
) -> None:
    asset = create_asset(client, tenant_a, title="keep.xlsx")
    sealed = upload_sealed_version(client, tenant_a, asset["id"], b"payload")

    refused = client.delete(f"{CONTENT}/assets/{asset['id']}", headers=tenant_a.headers)
    assert refused.status_code == 409, refused.text
    body = refused.json()
    assert body["code"] == "CONTENT_ARCHIVE_UNAVAILABLE"
    assert body["blocker"] == "content_assets_archive_columns_missing"
    assert "archived_at" in body["detail"], "the blocker names the missing column"
    dependents = body["dependents"]
    assert dependents["asset_id"] == asset["id"]
    assert dependents["version_count"] == 1
    assert dependents["safe_to_purge"] is True

    still_there = client.get(f"{CONTENT}/assets/{asset['id']}", headers=tenant_a.headers)
    assert still_there.status_code == 200, still_there.text
    version = client.get(
        f"{CONTENT}/assets/{asset['id']}/versions/{sealed['id']}", headers=tenant_a.headers
    )
    assert version.status_code == 200, "a refused archive must not touch versions"


@requires_postgres
def test_dependents_report_is_admin_scoped_and_reports_evidence(
    client: TestClient, tenant_a: Tenant
) -> None:
    asset = create_asset(client, tenant_a, visibility="private")
    member = tenant_a.add_member(client, login="evidence-member")

    report = _dependents(client, tenant_a, asset["id"])
    assert report.status_code == 200, report.text

    by_member = client.get(
        f"{CONTENT}/assets/{asset['id']}/dependents", headers=member.headers
    )
    assert by_member.status_code == 403, by_member.text
    assert by_member.json()["code"] == "FORBIDDEN"

    body = report.json()
    assert set(body) == {
        "asset_id",
        "version_count",
        "active_alias_bindings",
        "binding_ids",
        "binding_count",
        "asset_links",
        "safe_to_purge",
        "archive_available",
        "archive_blocker",
        "evidence_policy",
    }
    assert body["archive_available"] is False
    assert body["archive_blocker"] == "CONTENT_ARCHIVE_UNAVAILABLE"
    assert body["version_count"] == 0
    assert body["binding_ids"] == []
    assert body["asset_links"] == []


@requires_postgres
def test_dependents_of_a_foreign_tenant_asset_is_404(
    client: TestClient, tenant_a: Tenant, tenant_b: Tenant
) -> None:
    asset = create_asset(client, tenant_a)
    response = _dependents(client, tenant_b, asset["id"])
    assert response.status_code == 404, response.text
    assert response.json()["code"] == "NOT_FOUND"

    unknown = _dependents(client, tenant_a, "cast_missing")
    assert unknown.status_code == 404, unknown.text


@requires_postgres
def test_dependents_report_counts_alias_binding_history(
    client: TestClient, tenant_a: Tenant
) -> None:
    asset = create_asset(client, tenant_a)
    first = upload_sealed_version(client, tenant_a, asset["id"], b"one")
    second = upload_sealed_version(client, tenant_a, asset["id"], b"two")
    alias = client.post(
        f"{CONTENT}/aliases",
        headers=tenant_a.headers,
        json={"slug": "evidence.alias", "owner_company_id": tenant_a.company_id},
    ).json()
    client.post(
        f"{CONTENT}/aliases/{alias['id']}/bind",
        headers=tenant_a.headers,
        json={"asset_id": asset["id"], "blob_version_id": first["id"]},
    )
    client.post(
        f"{CONTENT}/aliases/{alias['id']}/bind",
        headers=tenant_a.headers,
        json={"asset_id": asset["id"], "blob_version_id": second["id"]},
    )

    body = _dependents(client, tenant_a, asset["id"]).json()
    assert body["version_count"] == 2
    assert len(body["active_alias_bindings"]) == 1
    assert body["binding_count"] == 2, "superseded bindings are evidence too"
    assert set(body["active_alias_bindings"]) < set(body["binding_ids"])
    assert body["safe_to_purge"] is False


@requires_postgres
def test_tenant_admin_cannot_purge(client: TestClient, tenant_a: Tenant) -> None:
    asset = create_asset(client, tenant_a)

    denied = client.delete(
        f"{CONTENT}/assets/{asset['id']}", headers=tenant_a.headers, params={"purge": "true"}
    )
    assert denied.status_code == 403, denied.text
    assert denied.json()["code"] == "FORBIDDEN"

    assert client.get(f"{CONTENT}/assets/{asset['id']}", headers=tenant_a.headers).status_code == 200


@requires_postgres
def test_platform_admin_purge_refuses_assets_with_binding_history(
    client: TestClient, tenant_a: Tenant
) -> None:
    asset = create_asset(client, tenant_a)
    sealed = upload_sealed_version(client, tenant_a, asset["id"], b"bound")
    alias = client.post(
        f"{CONTENT}/aliases",
        headers=tenant_a.headers,
        json={"slug": "purge.alias", "owner_company_id": tenant_a.company_id},
    ).json()
    client.post(
        f"{CONTENT}/aliases/{alias['id']}/bind",
        headers=tenant_a.headers,
        json={"asset_id": asset["id"], "blob_version_id": sealed["id"]},
    )

    for cascade in ("false", "true"):
        refused = client.delete(
            f"{CONTENT}/assets/{asset['id']}",
            headers=platform_headers(),
            params={"purge": "true", "cascade": cascade},
        )
        assert refused.status_code == 409, refused.text
        assert refused.json()["code"] == "CONTENT_DELETE_HAS_DEPENDENTS"
        assert refused.json()["binding_ids"], "the refusal names the retained evidence"

    assert client.get(f"{CONTENT}/assets/{asset['id']}", headers=tenant_a.headers).status_code == 200
    history = client.get(f"{CONTENT}/aliases/{alias['id']}/bindings", headers=tenant_a.headers)
    assert len(history.json()["items"]) == 1, "binding history must survive the refusal"


@requires_postgres
def test_purge_of_a_linked_asset_requires_cascade(
    client: TestClient, tenant_a: Tenant
) -> None:
    scoped = tenant_a.with_project(client, "Purge Project")
    asset = create_asset(client, tenant_a)
    linked = client.post(
        f"{CONTENT}/assets/{asset['id']}/links",
        headers=tenant_a.headers,
        json={"link_kind": "project", "link_id": scoped.project_id},
    )
    assert linked.status_code == 200, linked.text

    refused = client.delete(
        f"{CONTENT}/assets/{asset['id']}", headers=platform_headers(), params={"purge": "true"}
    )
    assert refused.status_code == 409, refused.text
    assert refused.json()["code"] == "CONTENT_DELETE_HAS_DEPENDENTS"
    assert refused.json()["asset_links"] == [linked.json()["id"]]

    purged = client.delete(
        f"{CONTENT}/assets/{asset['id']}",
        headers=platform_headers(),
        params={"purge": "true", "cascade": "true"},
    )
    assert purged.status_code == 200, purged.text
    body = purged.json()
    assert body["deleted"] is True
    assert body["id"] == asset["id"]

    assert client.get(f"{CONTENT}/assets/{asset['id']}", headers=tenant_a.headers).status_code == 404


@requires_postgres
def test_platform_admin_can_purge_a_clean_asset(client: TestClient, tenant_a: Tenant) -> None:
    asset = create_asset(client, tenant_a)
    upload_sealed_version(client, tenant_a, asset["id"], b"bytes")

    purged = client.delete(
        f"{CONTENT}/assets/{asset['id']}", headers=platform_headers(), params={"purge": "true"}
    )
    assert purged.status_code == 200, purged.text
    body = purged.json()
    assert body["deleted"] is True
    assert body["deleted_versions"] == 1
    assert body["dependents"]["version_count"] == 1

    assert client.get(f"{CONTENT}/assets/{asset['id']}", headers=tenant_a.headers).status_code == 404
    versions = client.get(f"{CONTENT}/assets/{asset['id']}/versions", headers=tenant_a.headers)
    assert versions.status_code == 404, versions.text
