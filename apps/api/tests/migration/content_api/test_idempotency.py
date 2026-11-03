"""Content API — ``Idempotency-Key`` on asset and alias creation.

Covers: same key + same body replays the original resource (no second row),
same key + different body is ``409 IDEMPOTENCY_REPLAY`` (never an overwrite),
a key longer than the limit is rejected, a replay reflects state written after
the first call (latest sealed version), and a *different* key that reuses an
alias slug still conflicts on the slug.

There is no content-scoped receipt table (``contracts.IDEMPOTENCY_BLOCKER``), so
these assertions are exactly the observable contract: the id is derived from
``(actor, tenant, key)`` and the persisted request projection is compared.
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
    upload_sealed_version,
)


def _post_asset(client: TestClient, tenant: Tenant, *, key: str | None, **body):
    headers = dict(tenant.headers)
    if key is not None:
        headers["Idempotency-Key"] = key
    payload = {"owner_company_id": tenant.company_id, "visibility": "company", **body}
    return client.post(f"{CONTENT}/assets", headers=headers, json=payload)


def _post_alias(client: TestClient, tenant: Tenant, *, key: str | None, **body):
    headers = dict(tenant.headers)
    if key is not None:
        headers["Idempotency-Key"] = key
    payload = {"owner_company_id": tenant.company_id, **body}
    return client.post(f"{CONTENT}/aliases", headers=headers, json=payload)


def _list_assets(client: TestClient, tenant: Tenant) -> list[dict]:
    response = client.get(
        f"{CONTENT}/assets",
        headers=tenant.headers,
        params={"company_id": tenant.company_id, "limit": 200},
    )
    assert response.status_code == 200, response.text
    return response.json()["items"]


@requires_postgres
def test_asset_create_replays_the_same_key_and_body(client: TestClient, tenant_a: Tenant) -> None:
    first = _post_asset(client, tenant_a, key="asset-key-1", title="spec.xlsx", tags=["b", "a"])
    assert first.status_code == 200, first.text
    second = _post_asset(client, tenant_a, key="asset-key-1", title="spec.xlsx", tags=["a", "b", "a"])

    assert second.status_code == 200, second.text
    assert second.json()["id"] == first.json()["id"], "a replay must return the original resource"
    assert len(_list_assets(client, tenant_a)) == 1, "a replay must not create a second asset"


@requires_postgres
def test_asset_create_with_a_changed_body_is_a_conflict(
    client: TestClient, tenant_a: Tenant
) -> None:
    first = _post_asset(client, tenant_a, key="asset-key-2", title="spec.xlsx")
    assert first.status_code == 200, first.text

    conflicting = _post_asset(client, tenant_a, key="asset-key-2", title="other.xlsx")
    assert conflicting.status_code == 409, conflicting.text
    assert conflicting.json()["code"] == "IDEMPOTENCY_REPLAY"

    items = _list_assets(client, tenant_a)
    assert [item["id"] for item in items] == [first.json()["id"]]
    assert items[0]["title"] == "spec.xlsx", "a conflicting replay must not overwrite the row"


@requires_postgres
def test_asset_create_with_a_different_key_is_a_new_asset(
    client: TestClient, tenant_a: Tenant
) -> None:
    first = _post_asset(client, tenant_a, key="asset-key-3a", title="one")
    second = _post_asset(client, tenant_a, key="asset-key-3b", title="one")
    assert first.status_code == second.status_code == 200
    assert first.json()["id"] != second.json()["id"]
    assert len(_list_assets(client, tenant_a)) == 2


@requires_postgres
def test_asset_create_without_a_key_is_never_deduplicated(
    client: TestClient, tenant_a: Tenant
) -> None:
    first = _post_asset(client, tenant_a, key=None, title="same")
    second = _post_asset(client, tenant_a, key=None, title="same")
    assert first.json()["id"] != second.json()["id"]


@requires_postgres
def test_over_long_idempotency_key_is_rejected(client: TestClient, tenant_a: Tenant) -> None:
    response = _post_asset(client, tenant_a, key="k" * 129, title="too-long-key")
    assert response.status_code == 422, response.text
    assert response.json()["code"] == "VALIDATION_ERROR"
    assert _list_assets(client, tenant_a) == []


@requires_postgres
def test_asset_replay_reports_state_written_after_the_first_call(
    client: TestClient, tenant_a: Tenant
) -> None:
    created = _post_asset(client, tenant_a, key="asset-key-4", title="growing")
    assert created.status_code == 200, created.text
    asset_id = created.json()["id"]
    assert created.json()["latest_version"] is None

    sealed = upload_sealed_version(client, tenant_a, asset_id, b"v1")

    replayed = _post_asset(client, tenant_a, key="asset-key-4", title="growing")
    assert replayed.status_code == 200, replayed.text
    body = replayed.json()
    assert body["id"] == asset_id
    assert body["latest_version"] == sealed["version"] == 1
    assert body["latest_version_id"] == sealed["id"]


@requires_postgres
def test_alias_create_replays_the_same_key_and_rejects_a_changed_body(
    client: TestClient, tenant_a: Tenant
) -> None:
    first = _post_alias(client, tenant_a, key="alias-key-1", slug="idem.alias", label="Docs")
    assert first.status_code == 200, first.text

    replay = _post_alias(client, tenant_a, key="alias-key-1", slug="idem.alias", label="Docs")
    assert replay.status_code == 200, replay.text
    assert replay.json()["id"] == first.json()["id"]

    changed = _post_alias(client, tenant_a, key="alias-key-1", slug="idem.alias", label="Other")
    assert changed.status_code == 409, changed.text
    assert changed.json()["code"] == "IDEMPOTENCY_REPLAY"

    fetched = client.get(f"{CONTENT}/aliases/{first.json()['id']}", headers=tenant_a.headers)
    assert fetched.status_code == 200, fetched.text
    assert fetched.json()["label"] == "Docs", "a conflicting replay must not rewrite metadata"


@requires_postgres
def test_alias_slug_conflict_is_independent_of_the_idempotency_key(
    client: TestClient, tenant_a: Tenant
) -> None:
    assert _post_alias(
        client, tenant_a, key="alias-key-2a", slug="taken.twice"
    ).status_code == 200

    second = _post_alias(client, tenant_a, key="alias-key-2b", slug="taken.twice")
    assert second.status_code == 409, second.text
    assert second.json()["code"] == "CONFLICT"


@requires_postgres
def test_idempotency_key_is_scoped_to_the_creating_actor(
    client: TestClient, tenant_a: Tenant
) -> None:
    created = create_asset(client, tenant_a, idempotency_key="shared-key")
    member = tenant_a.add_member(client, login="idem-member")

    other_actor = client.post(
        f"{CONTENT}/assets",
        headers={**member.headers, "Idempotency-Key": "shared-key"},
        json={
            "owner_company_id": tenant_a.company_id,
            "visibility": "company",
            "title": "spec.xlsx",
            "mime": "application/octet-stream",
            "tags": [],
        },
    )
    assert other_actor.status_code == 200, other_actor.text
    assert other_actor.json()["id"] != created["id"], "the id includes the acting employee"
