"""Content API — asset create/read/list/patch and tenant isolation.

Covers: creation, listing (incl. cursor pagination), metadata patch, platform
ownership rules, and the crossing cases — foreign tenant on read, foreign tenant
on list, foreign tenant on write, anonymous caller.
"""

from __future__ import annotations

import pytest
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
)


@requires_postgres
def test_create_asset_is_owned_by_the_validated_tenant(
    client: TestClient, tenant_a: Tenant
) -> None:
    body = create_asset(client, tenant_a, title="spec.xlsx", mime="text/plain", tags=["b", "a"])

    assert body["owner_company_id"] == tenant_a.company_id
    assert body["owner_scope"] == "company"
    assert body["visibility"] == "company"
    assert body["title"] == "spec.xlsx"
    assert body["tags"] == ["a", "b"], "tags are normalised server-side"
    assert body["created_by"] == tenant_a.employee_id
    assert body["latest_version"] is None
    assert body["created_at"] and body["updated_at"]


@requires_postgres
def test_client_cannot_supply_owner_scope(client: TestClient, tenant_a: Tenant) -> None:
    response = client.post(
        f"{CONTENT}/assets",
        headers=tenant_a.headers,
        json={
            "owner_company_id": tenant_a.company_id,
            "owner_scope": "platform",
            "visibility": "company",
        },
    )
    assert response.status_code == 422, response.text
    assert response.json()["code"] == "VALIDATION_ERROR"


@requires_postgres
def test_company_tenant_cannot_create_a_platform_owned_asset(
    client: TestClient, tenant_a: Tenant
) -> None:
    response = client.post(
        f"{CONTENT}/assets",
        headers=tenant_a.headers,
        json={"owner_company_id": None, "visibility": "company", "title": "shared"},
    )
    assert response.status_code == 403, response.text
    assert response.json()["code"] == "FORBIDDEN"


@requires_postgres
def test_platform_admin_can_create_a_platform_owned_asset(
    client: TestClient, tenant_a: Tenant
) -> None:
    response = client.post(
        f"{CONTENT}/assets",
        headers=platform_headers(),
        json={"owner_company_id": None, "visibility": "company", "title": "published"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["owner_scope"] == "platform"
    assert body["owner_company_id"] is None


@requires_postgres
def test_read_asset_returns_latest_version_identity(
    client: TestClient, tenant_a: Tenant
) -> None:
    from tests.migration.content_api.fixtures import upload_sealed_version

    asset = create_asset(client, tenant_a)
    version = upload_sealed_version(client, tenant_a, asset["id"], b"hello")

    fetched = client.get(f"{CONTENT}/assets/{asset['id']}", headers=tenant_a.headers)
    assert fetched.status_code == 200, fetched.text
    body = fetched.json()
    assert body["latest_version"] == version["version"] == 1
    assert body["latest_version_id"] == version["id"]


@requires_postgres
def test_list_assets_is_scoped_to_readable_tenant_assets(
    client: TestClient, tenant_a: Tenant, tenant_b: Tenant
) -> None:
    mine = create_asset(client, tenant_a, title="mine.xlsx")
    other = create_asset(client, tenant_b, title="other.xlsx")

    listed = client.get(
        f"{CONTENT}/assets",
        headers=tenant_a.headers,
        params={"company_id": tenant_a.company_id},
    )
    assert listed.status_code == 200, listed.text
    ids = {item["id"] for item in listed.json()["items"]}
    assert mine["id"] in ids
    assert other["id"] not in ids
    assert all(
        item["owner_company_id"] == tenant_a.company_id
        for item in listed.json()["items"]
    )


@requires_postgres
def test_list_assets_for_a_foreign_company_is_404(
    client: TestClient, tenant_a: Tenant, tenant_b: Tenant
) -> None:
    response = client.get(
        f"{CONTENT}/assets",
        headers=tenant_a.headers,
        params={"company_id": tenant_b.company_id},
    )
    assert response.status_code == 404, response.text
    assert response.json()["code"] == "NOT_FOUND"


@requires_postgres
def test_list_assets_paginates_with_a_stable_cursor(
    client: TestClient, tenant_a: Tenant
) -> None:
    created = [create_asset(client, tenant_a, title=f"file-{index}.bin") for index in range(3)]

    first = client.get(
        f"{CONTENT}/assets",
        headers=tenant_a.headers,
        params={"company_id": tenant_a.company_id, "limit": 2},
    )
    assert first.status_code == 200, first.text
    first_body = first.json()
    assert len(first_body["items"]) == 2
    assert first_body["has_more"] is True
    assert first_body["next_cursor"]

    second = client.get(
        f"{CONTENT}/assets",
        headers=tenant_a.headers,
        params={
            "company_id": tenant_a.company_id,
            "limit": 2,
            "cursor": first_body["next_cursor"],
        },
    )
    assert second.status_code == 200, second.text
    second_body = second.json()
    assert len(second_body["items"]) == 1
    assert second_body["has_more"] is False
    assert second_body["next_cursor"] is None

    seen = [item["id"] for item in first_body["items"] + second_body["items"]]
    assert sorted(seen) == sorted(item["id"] for item in created)
    assert len(seen) == len(set(seen)), "a page must not repeat a row"


@requires_postgres
def test_malformed_cursor_is_rejected(client: TestClient, tenant_a: Tenant) -> None:
    response = client.get(
        f"{CONTENT}/assets",
        headers=tenant_a.headers,
        params={"company_id": tenant_a.company_id, "cursor": "not-a-cursor"},
    )
    assert response.status_code == 422, response.text
    assert response.json()["code"] == "VALIDATION_ERROR"


@requires_postgres
def test_patch_asset_updates_metadata_only(client: TestClient, tenant_a: Tenant) -> None:
    asset = create_asset(client, tenant_a, title="old")

    patched = client.patch(
        f"{CONTENT}/assets/{asset['id']}",
        headers=tenant_a.headers,
        json={"title": "new", "tags": ["z", "a"], "visibility": "private"},
    )
    assert patched.status_code == 200, patched.text
    body = patched.json()
    assert body["title"] == "new"
    assert body["tags"] == ["a", "z"]
    assert body["visibility"] == "private"
    assert body["owner_company_id"] == tenant_a.company_id
    assert body["id"] == asset["id"]


@requires_postgres
def test_patch_asset_rejects_unknown_field(client: TestClient, tenant_a: Tenant) -> None:
    asset = create_asset(client, tenant_a)
    response = client.patch(
        f"{CONTENT}/assets/{asset['id']}",
        headers=tenant_a.headers,
        json={"owner_company_id": tenant_a.company_id},
    )
    assert response.status_code == 422, response.text


@requires_postgres
def test_cross_tenant_read_is_404_not_403(client: TestClient, tenant_a: Tenant, tenant_b: Tenant) -> None:
    asset = create_asset(client, tenant_a)

    read = client.get(f"{CONTENT}/assets/{asset['id']}", headers=tenant_b.headers)
    assert read.status_code == 404, read.text
    assert read.json()["code"] == "NOT_FOUND"

    patched = client.patch(
        f"{CONTENT}/assets/{asset['id']}", headers=tenant_b.headers, json={"title": "hijack"}
    )
    assert patched.status_code == 404, patched.text

    acl = client.get(f"{CONTENT}/assets/{asset['id']}/acl", headers=tenant_b.headers)
    assert acl.status_code == 404, acl.text

    versions = client.get(f"{CONTENT}/assets/{asset['id']}/versions", headers=tenant_b.headers)
    assert versions.status_code == 404, versions.text

    download = client.get(
        f"{CONTENT}/assets/{asset['id']}/download", headers=tenant_b.headers, params={"proxy": "true"}
    )
    assert download.status_code == 404, download.text


@requires_postgres
def test_unknown_asset_id_is_404(client: TestClient, tenant_a: Tenant) -> None:
    response = client.get(f"{CONTENT}/assets/cast_doesnotexist", headers=tenant_a.headers)
    assert response.status_code == 404, response.text
    assert response.json()["code"] == "NOT_FOUND"


@requires_postgres
def test_missing_bearer_is_401(client: TestClient, tenant_a: Tenant) -> None:
    asset = create_asset(client, tenant_a)
    response = client.get(f"{CONTENT}/assets/{asset['id']}")
    assert response.status_code == 401, response.text


@requires_postgres
def test_invalid_visibility_is_rejected(client: TestClient, tenant_a: Tenant) -> None:
    response = client.post(
        f"{CONTENT}/assets",
        headers=tenant_a.headers,
        json={"owner_company_id": tenant_a.company_id, "visibility": "world"},
    )
    assert response.status_code == 422, response.text
    assert response.json()["code"] == "VALIDATION_ERROR"


@requires_postgres
def test_create_asset_requires_an_employee_session(client: TestClient, tenant_a: Tenant) -> None:
    """A platform.admin JWT without an employee row may still act, but a plain
    JWT with no employee must not be able to create tenant content."""
    from tests.migration.content_api.fixtures import token

    anonymous = {
        "Authorization": f"Bearer {token(sub='ghost-sub', roles=['employee'])}"
    }
    response = client.post(
        f"{CONTENT}/assets",
        headers=anonymous,
        json={"owner_company_id": tenant_a.company_id},
    )
    assert response.status_code == 403, response.text
    assert response.json()["code"] == "FORBIDDEN"
