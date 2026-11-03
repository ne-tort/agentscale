"""Content API — aborting a staged (unsealed) blob version.

A staged version exists between ``begin`` and ``upload``/``finalize``. When the
upload is cancelled the row and its presigned key would otherwise leak, and the
web client has no way to discard them (``content-ops`` README records this gap).

Contract:

* ``DELETE /content/assets/{id}/versions/{vid}`` removes an **unsealed** row and
  its stored bytes, freeing the version number for the next ``begin``;
* a **sealed** version is immutable evidence and is refused with
  ``409 CONTENT_VERSION_SEALED`` — new bytes always require a new version;
* a version of another asset, another tenant, or an ungranted private asset is
  ``404``/``403`` exactly like every other version route.
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
    upload_version,
)


def _abort(client: TestClient, tenant: Tenant, asset_id: str, version_id: str):
    return client.delete(
        f"{CONTENT}/assets/{asset_id}/versions/{version_id}", headers=tenant.headers
    )


@requires_postgres
def test_abort_removes_a_staged_version_and_frees_the_number(
    client: TestClient, tenant_a: Tenant
) -> None:
    asset = create_asset(client, tenant_a)
    staged = begin_version(client, tenant_a, asset["id"])

    aborted = _abort(client, tenant_a, asset["id"], staged["version"]["id"])
    assert aborted.status_code == 200, aborted.text
    body = aborted.json()
    assert body["aborted"] is True
    assert body["asset_id"] == asset["id"]
    assert body["version_id"] == staged["version"]["id"]
    assert body["version"] == 1

    gone = client.get(
        f"{CONTENT}/assets/{asset['id']}/versions/{staged['version']['id']}",
        headers=tenant_a.headers,
    )
    assert gone.status_code == 404, gone.text

    again = begin_version(client, tenant_a, asset["id"])
    assert again["version"]["version"] == 1, "an aborted staged version frees its number"
    assert again["version"]["id"] != staged["version"]["id"]


@requires_postgres
def test_abort_keeps_sealed_versions_and_refuses_them(
    client: TestClient, tenant_a: Tenant
) -> None:
    asset = create_asset(client, tenant_a, title="sealed.bin")
    sealed = upload_sealed_version(client, tenant_a, asset["id"], b"immutable")

    refused = _abort(client, tenant_a, asset["id"], sealed["id"])
    assert refused.status_code == 409, refused.text
    body = refused.json()
    assert body["code"] == "CONTENT_VERSION_SEALED"
    assert body["sha256"] == sealed["sha256"]

    detail = client.get(
        f"{CONTENT}/assets/{asset['id']}/versions/{sealed['id']}", headers=tenant_a.headers
    )
    assert detail.status_code == 200, detail.text
    download = client.get(
        f"{CONTENT}/assets/{asset['id']}/download",
        headers=tenant_a.headers,
        params={"blob_version_id": sealed["id"], "proxy": "true"},
    )
    assert download.status_code == 200, download.text
    assert download.content == b"immutable"


@requires_postgres
def test_abort_after_a_failed_upload_leaves_nothing_behind(
    client: TestClient, tenant_a: Tenant
) -> None:
    asset = create_asset(client, tenant_a)
    staged = begin_version(client, tenant_a, asset["id"])
    version_id = staged["version"]["id"]
    rejected = upload_version(
        client, tenant_a, asset["id"], version_id, b"payload", expected_sha256="0" * 64
    )
    assert rejected.status_code == 422, rejected.text

    aborted = _abort(client, tenant_a, asset["id"], version_id)
    assert aborted.status_code == 200, aborted.text

    history = client.get(f"{CONTENT}/assets/{asset['id']}/versions", headers=tenant_a.headers)
    assert history.status_code == 200, history.text
    assert history.json()["items"] == [], "a rejected upload leaves no version row"


@requires_postgres
def test_abort_of_a_version_owned_by_another_asset_is_404(
    client: TestClient, tenant_a: Tenant
) -> None:
    owner = create_asset(client, tenant_a, title="owner")
    other = create_asset(client, tenant_a, title="other")
    staged = begin_version(client, tenant_a, owner["id"])

    response = _abort(client, tenant_a, other["id"], staged["version"]["id"])
    assert response.status_code == 404, response.text
    assert response.json()["code"] == "NOT_FOUND"


@requires_postgres
def test_cross_tenant_abort_is_404(client: TestClient, tenant_a: Tenant, tenant_b: Tenant) -> None:
    asset = create_asset(client, tenant_a)
    staged = begin_version(client, tenant_a, asset["id"])

    response = _abort(client, tenant_b, asset["id"], staged["version"]["id"])
    assert response.status_code == 404, response.text

    still_open = client.get(
        f"{CONTENT}/assets/{asset['id']}/versions/{staged['version']['id']}",
        headers=tenant_a.headers,
    )
    assert still_open.status_code == 200, "a foreign caller must not abort the upload"


@requires_postgres
def test_read_only_member_cannot_abort_a_private_asset_upload(
    client: TestClient, tenant_a: Tenant
) -> None:
    asset = create_asset(client, tenant_a, visibility="private")
    member = tenant_a.add_member(client, login="abort-reader")
    granted = client.put(
        f"{CONTENT}/assets/{asset['id']}/acl",
        headers=tenant_a.headers,
        json={
            "entries": [
                {"principal_kind": "employee", "principal_id": member.employee_id, "permission": "read"}
            ]
        },
    )
    assert granted.status_code == 200, granted.text
    staged = begin_version(client, tenant_a, asset["id"])

    denied = client.delete(
        f"{CONTENT}/assets/{asset['id']}/versions/{staged['version']['id']}",
        headers=member.headers,
    )
    assert denied.status_code == 403, denied.text
    assert denied.json()["code"] == "FORBIDDEN"
