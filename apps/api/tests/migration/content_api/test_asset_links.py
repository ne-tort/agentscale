"""Content API — asset ↔ domain entity links (``content_asset_links``).

Covers: caller-assignable kinds only, tenant-scoped target validation, the
1:1 ``(link_kind, link_id)`` constraint, explicit ``replace`` re-pointing,
unlink, and cross-tenant/IDOR cases.
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
)


@requires_postgres
def test_link_asset_to_project_and_list(
    client: TestClient, tenant_a: Tenant
) -> None:
    scoped = tenant_a.with_project(client, "Linked Project")
    asset = create_asset(client, tenant_a)

    linked = client.post(
        f"{CONTENT}/assets/{asset['id']}/links",
        headers=tenant_a.headers,
        json={"link_kind": "project", "link_id": scoped.project_id},
    )
    assert linked.status_code == 200, linked.text
    body = linked.json()
    assert body["link_kind"] == "project"
    assert body["link_id"] == scoped.project_id

    listed = client.get(f"{CONTENT}/assets/{asset['id']}/links", headers=tenant_a.headers)
    assert listed.status_code == 200, listed.text
    assert [item["id"] for item in listed.json()["items"]] == [body["id"]]


@requires_postgres
def test_linking_the_same_target_twice_is_a_replay(
    client: TestClient, tenant_a: Tenant
) -> None:
    scoped = tenant_a.with_project(client, "Replay Project")
    asset = create_asset(client, tenant_a)
    payload = {"link_kind": "project", "link_id": scoped.project_id}

    first = client.post(f"{CONTENT}/assets/{asset['id']}/links", headers=tenant_a.headers, json=payload)
    second = client.post(f"{CONTENT}/assets/{asset['id']}/links", headers=tenant_a.headers, json=payload)
    assert first.status_code == second.status_code == 200
    assert second.json()["id"] == first.json()["id"]
    assert second.json()["replayed"] is True


@requires_postgres
def test_same_target_bound_to_another_asset_conflicts(
    client: TestClient, tenant_a: Tenant
) -> None:
    scoped = tenant_a.with_project(client, "Conflict Project")
    owner = create_asset(client, tenant_a, title="owner")
    contender = create_asset(client, tenant_a, title="contender")
    payload = {"link_kind": "project", "link_id": scoped.project_id}

    assert client.post(
        f"{CONTENT}/assets/{owner['id']}/links", headers=tenant_a.headers, json=payload
    ).status_code == 200

    conflict = client.post(
        f"{CONTENT}/assets/{contender['id']}/links", headers=tenant_a.headers, json=payload
    )
    assert conflict.status_code == 409, conflict.text
    assert conflict.json()["code"] == "CONTENT_LINK_TARGET_CONFLICT"


@requires_postgres
def test_explicit_replace_repoints_the_edge(client: TestClient, tenant_a: Tenant) -> None:
    scoped = tenant_a.with_project(client, "Repoint Project")
    owner = create_asset(client, tenant_a, title="owner")
    contender = create_asset(client, tenant_a, title="contender")
    payload = {"link_kind": "project", "link_id": scoped.project_id}

    client.post(f"{CONTENT}/assets/{owner['id']}/links", headers=tenant_a.headers, json=payload)
    replaced = client.post(
        f"{CONTENT}/assets/{contender['id']}/links",
        headers=tenant_a.headers,
        json={**payload, "replace": True},
    )
    assert replaced.status_code == 200, replaced.text

    old_links = client.get(f"{CONTENT}/assets/{owner['id']}/links", headers=tenant_a.headers)
    assert old_links.json()["items"] == []
    new_links = client.get(f"{CONTENT}/assets/{contender['id']}/links", headers=tenant_a.headers)
    assert len(new_links.json()["items"]) == 1


@requires_postgres
def test_documents_link_kind_is_not_caller_assignable(
    client: TestClient, tenant_a: Tenant
) -> None:
    asset = create_asset(client, tenant_a)

    rejected = client.post(
        f"{CONTENT}/assets/{asset['id']}/links",
        headers=tenant_a.headers,
        json={"link_kind": "documents", "link_id": "doc_1"},
    )
    assert rejected.status_code == 422, rejected.text
    assert rejected.json()["code"] == "VALIDATION_ERROR"


@requires_postgres
def test_link_to_a_project_of_another_tenant_is_404(
    client: TestClient, tenant_a: Tenant, tenant_b: Tenant
) -> None:
    foreign = tenant_b.with_project(client, "Foreign Project")
    asset = create_asset(client, tenant_a)

    rejected = client.post(
        f"{CONTENT}/assets/{asset['id']}/links",
        headers=tenant_a.headers,
        json={"link_kind": "project", "link_id": foreign.project_id},
    )
    assert rejected.status_code == 404, rejected.text
    assert rejected.json()["code"] == "NOT_FOUND"


@requires_postgres
def test_link_to_a_missing_project_is_404(client: TestClient, tenant_a: Tenant) -> None:
    asset = create_asset(client, tenant_a)
    rejected = client.post(
        f"{CONTENT}/assets/{asset['id']}/links",
        headers=tenant_a.headers,
        json={"link_kind": "project", "link_id": "prj_missing"},
    )
    assert rejected.status_code == 404, rejected.text


@requires_postgres
def test_cross_tenant_asset_links_are_404(
    client: TestClient, tenant_a: Tenant, tenant_b: Tenant
) -> None:
    scoped = tenant_a.with_project(client, "Owner Project")
    asset = create_asset(client, tenant_a)
    client.post(
        f"{CONTENT}/assets/{asset['id']}/links",
        headers=tenant_a.headers,
        json={"link_kind": "project", "link_id": scoped.project_id},
    )

    listed = client.get(f"{CONTENT}/assets/{asset['id']}/links", headers=tenant_b.headers)
    assert listed.status_code == 404, listed.text

    created = client.post(
        f"{CONTENT}/assets/{asset['id']}/links",
        headers=tenant_b.headers,
        json={"link_kind": "project", "link_id": scoped.project_id},
    )
    assert created.status_code == 404, created.text


@requires_postgres
def test_unlink_removes_the_edge(client: TestClient, tenant_a: Tenant) -> None:
    scoped = tenant_a.with_project(client, "Unlink Project")
    asset = create_asset(client, tenant_a)
    link = client.post(
        f"{CONTENT}/assets/{asset['id']}/links",
        headers=tenant_a.headers,
        json={"link_kind": "project", "link_id": scoped.project_id},
    ).json()

    removed = client.delete(
        f"{CONTENT}/assets/{asset['id']}/links/{link['id']}", headers=tenant_a.headers
    )
    assert removed.status_code == 200, removed.text
    assert removed.json()["unlinked"] is True

    listed = client.get(f"{CONTENT}/assets/{asset['id']}/links", headers=tenant_a.headers)
    assert listed.json()["items"] == []

    again = client.delete(
        f"{CONTENT}/assets/{asset['id']}/links/{link['id']}", headers=tenant_a.headers
    )
    assert again.status_code == 404, again.text


@requires_postgres
def test_unlink_of_a_link_belonging_to_another_asset_is_404(
    client: TestClient, tenant_a: Tenant
) -> None:
    scoped = tenant_a.with_project(client, "Scoped Project")
    owner = create_asset(client, tenant_a, title="owner")
    other = create_asset(client, tenant_a, title="other")
    link = client.post(
        f"{CONTENT}/assets/{owner['id']}/links",
        headers=tenant_a.headers,
        json={"link_kind": "project", "link_id": scoped.project_id},
    ).json()

    rejected = client.delete(
        f"{CONTENT}/assets/{other['id']}/links/{link['id']}", headers=tenant_a.headers
    )
    assert rejected.status_code == 404, rejected.text


@requires_postgres
def test_platform_owned_asset_may_link_into_any_tenant_the_caller_owns(
    client: TestClient, tenant_a: Tenant
) -> None:
    published = client.post(
        f"{CONTENT}/assets",
        headers=platform_headers(),
        json={"owner_company_id": None, "title": "published-template"},
    )
    assert published.status_code == 200, published.text
    asset_id = published.json()["id"]
    scoped = tenant_a.with_project(client, "Consumer Project")

    linked = client.post(
        f"{CONTENT}/assets/{asset_id}/links",
        headers=platform_headers(),
        json={"link_kind": "project", "link_id": scoped.project_id},
    )
    assert linked.status_code == 200, linked.text
