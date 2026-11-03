"""Content API — ACL entries per principal kind and access enforcement.

Covers: private-asset default denial, employee/role/company/cabinet grants,
cross-tenant grant rejection, duplicate entries, unprivileged ACL rewrite
(403), ACL rewrite by platform admin, and that a revoked grant immediately
blocks access (no caching, no client-asserted authorization).
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


def _replace_acl(client: TestClient, tenant: Tenant, asset_id: str, entries: list[dict]):
    return client.put(
        f"{CONTENT}/assets/{asset_id}/acl",
        headers=tenant.headers,
        json={"entries": entries},
    )


@requires_postgres
def test_private_asset_is_invisible_to_a_same_tenant_member(
    client: TestClient, tenant_a: Tenant
) -> None:
    asset = create_asset(client, tenant_a, visibility="private")
    member = tenant_a.add_member(client, login="private-member")

    response = client.get(f"{CONTENT}/assets/{asset['id']}", headers=member.headers)
    assert response.status_code == 403, response.text
    assert response.json()["code"] == "FORBIDDEN"


@requires_postgres
def test_employee_grant_opens_a_private_asset(client: TestClient, tenant_a: Tenant) -> None:
    asset = create_asset(client, tenant_a, visibility="private")
    member = tenant_a.add_member(client, login="granted-member")

    granted = _replace_acl(
        client,
        tenant_a,
        asset["id"],
        [{"principal_kind": "employee", "principal_id": member.employee_id, "permission": "read"}],
    )
    assert granted.status_code == 200, granted.text
    assert granted.json()["entries"][0]["principal_kind"] == "employee"

    read = client.get(f"{CONTENT}/assets/{asset['id']}", headers=member.headers)
    assert read.status_code == 200, read.text

    # A read grant does not confer write.
    write = client.patch(
        f"{CONTENT}/assets/{asset['id']}", headers=member.headers, json={"title": "hijack"}
    )
    assert write.status_code == 403, write.text


@requires_postgres
def test_write_grant_confers_metadata_patch(client: TestClient, tenant_a: Tenant) -> None:
    asset = create_asset(client, tenant_a, visibility="private")
    member = tenant_a.add_member(client, login="writer-member")
    granted = _replace_acl(
        client,
        tenant_a,
        asset["id"],
        [{"principal_kind": "employee", "principal_id": member.employee_id, "permission": "write"}],
    )
    assert granted.status_code == 200, granted.text

    write = client.patch(
        f"{CONTENT}/assets/{asset['id']}", headers=member.headers, json={"title": "edited"}
    )
    assert write.status_code == 200, write.text
    assert write.json()["title"] == "edited"


@requires_postgres
def test_role_grant_matches_membership_roles(client: TestClient, tenant_a: Tenant) -> None:
    asset = create_asset(client, tenant_a, visibility="private")
    member = tenant_a.add_member(client, login="role-member")

    granted = _replace_acl(
        client,
        tenant_a,
        asset["id"],
        [{"principal_kind": "role", "principal_id": "member", "permission": "read"}],
    )
    assert granted.status_code == 200, granted.text

    read = client.get(f"{CONTENT}/assets/{asset['id']}", headers=member.headers)
    assert read.status_code == 200, read.text


@requires_postgres
def test_company_grant_matches_the_owning_tenant(client: TestClient, tenant_a: Tenant) -> None:
    asset = create_asset(client, tenant_a, visibility="private")
    member = tenant_a.add_member(client, login="company-grantee")

    granted = _replace_acl(
        client,
        tenant_a,
        asset["id"],
        [{"principal_kind": "company", "principal_id": tenant_a.company_id, "permission": "read"}],
    )
    assert granted.status_code == 200, granted.text

    read = client.get(f"{CONTENT}/assets/{asset['id']}", headers=member.headers)
    assert read.status_code == 200, read.text


@requires_postgres
def test_cabinet_grant_is_accepted_for_the_owning_tenant(
    client: TestClient, tenant_a: Tenant
) -> None:
    asset = create_asset(client, tenant_a, visibility="private")
    granted = _replace_acl(
        client,
        tenant_a,
        asset["id"],
        [
            {
                "principal_kind": "cabinet",
                "principal_id": tenant_a.cabinet_id,
                "permission": "read",
            }
        ],
    )
    assert granted.status_code == 200, granted.text
    assert granted.json()["entries"][0]["principal_id"] == tenant_a.cabinet_id


@requires_postgres
def test_cross_tenant_company_grant_is_rejected(
    client: TestClient, tenant_a: Tenant, tenant_b: Tenant
) -> None:
    asset = create_asset(client, tenant_a, visibility="private")

    rejected = _replace_acl(
        client,
        tenant_a,
        asset["id"],
        [{"principal_kind": "company", "principal_id": tenant_b.company_id, "permission": "read"}],
    )
    assert rejected.status_code == 422, rejected.text
    assert rejected.json()["code"] == "CONTENT_ACL_CROSS_TENANT"

    # Nothing was written by the rejected replace.
    listed = client.get(f"{CONTENT}/assets/{asset['id']}/acl", headers=tenant_a.headers)
    assert listed.status_code == 200, listed.text
    assert listed.json()["entries"] == []


@requires_postgres
def test_cross_tenant_cabinet_grant_is_rejected(
    client: TestClient, tenant_a: Tenant, tenant_b: Tenant
) -> None:
    asset = create_asset(client, tenant_a, visibility="private")

    rejected = _replace_acl(
        client,
        tenant_a,
        asset["id"],
        [
            {
                "principal_kind": "cabinet",
                "principal_id": tenant_b.cabinet_id,
                "permission": "read",
            }
        ],
    )
    assert rejected.status_code == 422, rejected.text
    assert rejected.json()["code"] == "CONTENT_ACL_CROSS_TENANT"


@requires_postgres
def test_employee_grant_for_a_foreign_employee_is_rejected(
    client: TestClient, tenant_a: Tenant, tenant_b: Tenant
) -> None:
    asset = create_asset(client, tenant_a, visibility="private")

    rejected = _replace_acl(
        client,
        tenant_a,
        asset["id"],
        [
            {
                "principal_kind": "employee",
                "principal_id": tenant_b.employee_id,
                "permission": "read",
            }
        ],
    )
    assert rejected.status_code == 422, rejected.text
    assert rejected.json()["code"] == "CONTENT_ACL_CROSS_TENANT"


@requires_postgres
def test_platform_grant_by_a_tenant_admin_is_rejected(
    client: TestClient, tenant_a: Tenant
) -> None:
    asset = create_asset(client, tenant_a, visibility="private")

    rejected = _replace_acl(
        client,
        tenant_a,
        asset["id"],
        [{"principal_kind": "platform", "principal_id": "platform", "permission": "read"}],
    )
    assert rejected.status_code == 422, rejected.text
    assert rejected.json()["code"] == "CONTENT_ACL_PLATFORM_GRANT_FORBIDDEN"


@requires_postgres
def test_duplicate_entries_are_rejected(client: TestClient, tenant_a: Tenant) -> None:
    asset = create_asset(client, tenant_a, visibility="private")
    entry = {
        "principal_kind": "company",
        "principal_id": tenant_a.company_id,
        "permission": "read",
    }

    rejected = _replace_acl(client, tenant_a, asset["id"], [entry, dict(entry)])
    assert rejected.status_code == 422, rejected.text
    assert rejected.json()["code"] == "CONTENT_ACL_DUPLICATE"


@requires_postgres
def test_unsupported_principal_kind_is_rejected_by_the_schema(
    client: TestClient, tenant_a: Tenant
) -> None:
    asset = create_asset(client, tenant_a, visibility="private")
    rejected = _replace_acl(
        client,
        tenant_a,
        asset["id"],
        [{"principal_kind": "tenant_group", "principal_id": "g1", "permission": "read"}],
    )
    assert rejected.status_code == 422, rejected.text
    assert rejected.json()["code"] == "VALIDATION_ERROR"


@requires_postgres
def test_unprivileged_member_cannot_rewrite_the_acl(
    client: TestClient, tenant_a: Tenant
) -> None:
    asset = create_asset(client, tenant_a, visibility="private")
    member = tenant_a.add_member(client, login="acl-member")

    denied = client.put(
        f"{CONTENT}/assets/{asset['id']}/acl",
        headers=member.headers,
        json={"entries": [{"principal_kind": "employee", "principal_id": member.employee_id}]},
    )
    assert denied.status_code == 403, denied.text
    assert denied.json()["code"] == "FORBIDDEN"


@requires_postgres
def test_read_grant_holder_cannot_escalate_the_acl(
    client: TestClient, tenant_a: Tenant
) -> None:
    asset = create_asset(client, tenant_a, visibility="private")
    member = tenant_a.add_member(client, login="escalating-member")
    _replace_acl(
        client,
        tenant_a,
        asset["id"],
        [{"principal_kind": "employee", "principal_id": member.employee_id, "permission": "read"}],
    )

    denied = client.put(
        f"{CONTENT}/assets/{asset['id']}/acl",
        headers=member.headers,
        json={"entries": [{"principal_kind": "employee", "principal_id": member.employee_id}]},
    )
    assert denied.status_code == 403, denied.text


@requires_postgres
def test_platform_admin_can_rewrite_any_tenant_acl(
    client: TestClient, tenant_a: Tenant
) -> None:
    asset = create_asset(client, tenant_a, visibility="private")

    granted = client.put(
        f"{CONTENT}/assets/{asset['id']}/acl",
        headers=platform_headers(),
        json={"entries": [{"principal_kind": "platform", "principal_id": "platform"}]},
    )
    assert granted.status_code == 200, granted.text
    assert granted.json()["entries"][0]["principal_kind"] == "platform"


@requires_postgres
def test_replacing_the_acl_revokes_previous_grants(
    client: TestClient, tenant_a: Tenant
) -> None:
    asset = create_asset(client, tenant_a, visibility="private")
    member = tenant_a.add_member(client, login="revoked-member")
    _replace_acl(
        client,
        tenant_a,
        asset["id"],
        [{"principal_kind": "employee", "principal_id": member.employee_id, "permission": "read"}],
    )
    assert client.get(f"{CONTENT}/assets/{asset['id']}", headers=member.headers).status_code == 200

    replaced = _replace_acl(client, tenant_a, asset["id"], [])
    assert replaced.status_code == 200, replaced.text
    assert replaced.json()["entries"] == []

    assert client.get(f"{CONTENT}/assets/{asset['id']}", headers=member.headers).status_code == 403


@requires_postgres
def test_acl_payload_is_stable_for_the_web_client(client: TestClient, tenant_a: Tenant) -> None:
    asset = create_asset(client, tenant_a, visibility="private")
    _replace_acl(
        client,
        tenant_a,
        asset["id"],
        [{"principal_kind": "company", "principal_id": tenant_a.company_id, "permission": "admin"}],
    )

    listed = client.get(f"{CONTENT}/assets/{asset['id']}/acl", headers=tenant_a.headers)
    assert listed.status_code == 200, listed.text
    body = listed.json()
    assert body["resource_kind"] == "asset"
    assert body["resource_id"] == asset["id"]
    entry = body["entries"][0]
    assert set(entry) == {"id", "principal_kind", "principal_id", "permission"}
    assert entry["permission"] == "admin"


@requires_postgres
def test_alias_acl_is_managed_separately_from_asset_acl(
    client: TestClient, tenant_a: Tenant
) -> None:
    created = client.post(
        f"{CONTENT}/aliases",
        headers=tenant_a.headers,
        json={"slug": "acl.alias", "owner_company_id": tenant_a.company_id},
    )
    assert created.status_code == 200, created.text
    alias_id = created.json()["id"]

    granted = client.put(
        f"{CONTENT}/aliases/{alias_id}/acl",
        headers=tenant_a.headers,
        json={"entries": [{"principal_kind": "company", "principal_id": tenant_a.company_id}]},
    )
    assert granted.status_code == 200, granted.text
    assert granted.json()["resource_kind"] == "alias"

    asset_acl = client.get(f"{CONTENT}/assets/{alias_id}/acl", headers=tenant_a.headers)
    assert asset_acl.status_code == 404, "an alias id must not resolve as an asset"


@requires_postgres
def test_cross_tenant_alias_acl_is_404(client: TestClient, tenant_a: Tenant, tenant_b: Tenant) -> None:
    created = client.post(
        f"{CONTENT}/aliases",
        headers=tenant_a.headers,
        json={"slug": "private.alias", "owner_company_id": tenant_a.company_id},
    )
    assert created.status_code == 200, created.text
    alias_id = created.json()["id"]

    listed = client.get(f"{CONTENT}/aliases/{alias_id}/acl", headers=tenant_b.headers)
    assert listed.status_code == 404, listed.text

    replaced = client.put(
        f"{CONTENT}/aliases/{alias_id}/acl",
        headers=tenant_b.headers,
        json={"entries": []},
    )
    assert replaced.status_code == 404, replaced.text
