"""Content API — immutable, digest-bound blob versions.

Covers: begin/upload/finalize, sealed-version rejection, digest mismatch on
upload and on finalize, undeclared-digest path, version history pagination,
exact-version download headers, NOT_READY downloads, and cross-tenant version
access.
"""

from __future__ import annotations

import hashlib

from fastapi.testclient import TestClient

from tests.conftest import requires_postgres
from tests.migration.content_api.fixtures import (
    CONTENT,
    Tenant,
    begin_version,
    client,
    create_asset,
    finalize_version,
    store_presigned_bytes,
    tenant_a,
    tenant_b,
    upload_sealed_version,
    upload_version,
)


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@requires_postgres
def test_begin_version_opens_an_unsealed_version(client: TestClient, tenant_a: Tenant) -> None:
    asset = create_asset(client, tenant_a)
    begun = begin_version(client, tenant_a, asset["id"], mime="text/plain")

    version = begun["version"]
    assert version["asset_id"] == asset["id"]
    assert version["version"] == 1
    assert version["sealed"] is False
    assert version["sha256"] is None
    assert version["size"] == 0
    assert begun["upload_url"]
    assert begun["method"] == "PUT"
    assert version["storage_key"], "kept for the existing integration test contract"


@requires_postgres
def test_version_numbers_increase_and_are_never_reused(
    client: TestClient, tenant_a: Tenant
) -> None:
    asset = create_asset(client, tenant_a)
    first = upload_sealed_version(client, tenant_a, asset["id"], b"one")
    second = upload_sealed_version(client, tenant_a, asset["id"], b"two")

    assert (first["version"], second["version"]) == (1, 2)
    assert first["id"] != second["id"]
    assert first["sha256"] == _sha(b"one")
    assert second["sha256"] == _sha(b"two")


@requires_postgres
def test_upload_seals_the_digest(client: TestClient, tenant_a: Tenant) -> None:
    asset = create_asset(client, tenant_a)
    begun = begin_version(client, tenant_a, asset["id"])
    version_id = begun["version"]["id"]

    uploaded = upload_version(client, tenant_a, asset["id"], version_id, b"body")
    assert uploaded.status_code == 200, uploaded.text
    body = uploaded.json()
    assert body["size"] == 4
    assert body["sha256"] == _sha(b"body")
    assert body["sealed"] is True
    assert body["object_metadata"]["sealed"] is True


@requires_postgres
def test_second_upload_into_a_sealed_version_is_rejected(
    client: TestClient, tenant_a: Tenant
) -> None:
    asset = create_asset(client, tenant_a)
    sealed = upload_sealed_version(client, tenant_a, asset["id"], b"original")

    retry = upload_version(client, tenant_a, asset["id"], sealed["id"], b"tampered")
    assert retry.status_code == 409, retry.text
    payload = retry.json()
    assert payload["code"] == "CONTENT_VERSION_SEALED"
    assert payload["sha256"] == sealed["sha256"]


@requires_postgres
def test_digest_declared_at_begin_must_match_the_uploaded_bytes(
    client: TestClient, tenant_a: Tenant
) -> None:
    asset = create_asset(client, tenant_a)
    expected = _sha(b"correct")
    begun = begin_version(client, tenant_a, asset["id"], sha256=expected)
    version_id = begun["version"]["id"]

    rejected = upload_version(client, tenant_a, asset["id"], version_id, b"wrong")
    assert rejected.status_code == 422, rejected.text
    assert rejected.json()["code"] == "CONTENT_DIGEST_MISMATCH"

    still_open = client.get(
        f"{CONTENT}/assets/{asset['id']}/versions/{version_id}", headers=tenant_a.headers
    )
    assert still_open.status_code == 200, still_open.text
    assert still_open.json()["sealed"] is False, "a rejected upload must not seal the version"

    accepted = upload_version(client, tenant_a, asset["id"], version_id, b"correct")
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["sha256"] == expected


@requires_postgres
def test_expected_digest_header_must_match_the_uploaded_bytes(
    client: TestClient, tenant_a: Tenant
) -> None:
    asset = create_asset(client, tenant_a)
    begun = begin_version(client, tenant_a, asset["id"])
    version_id = begun["version"]["id"]

    rejected = upload_version(
        client, tenant_a, asset["id"], version_id, b"payload", expected_sha256=_sha(b"other")
    )
    assert rejected.status_code == 422, rejected.text
    assert rejected.json()["code"] == "CONTENT_DIGEST_MISMATCH"


@requires_postgres
def test_malformed_expected_digest_is_a_validation_error(
    client: TestClient, tenant_a: Tenant
) -> None:
    asset = create_asset(client, tenant_a)
    begun = begin_version(client, tenant_a, asset["id"])
    version_id = begun["version"]["id"]

    rejected = upload_version(
        client, tenant_a, asset["id"], version_id, b"payload", expected_sha256="nothex"
    )
    assert rejected.status_code == 422, rejected.text
    assert rejected.json()["code"] == "VALIDATION_ERROR"


@requires_postgres
def test_finalize_seals_a_presigned_upload(client: TestClient, tenant_a: Tenant) -> None:
    asset = create_asset(client, tenant_a)
    begun = begin_version(client, tenant_a, asset["id"])
    version_id = begun["version"]["id"]
    store_presigned_bytes(begun, b"# content")

    finalized = finalize_version(client, tenant_a, asset["id"], version_id)
    assert finalized.status_code == 200, finalized.text
    body = finalized.json()
    assert body["size"] == len(b"# content")
    assert body["sha256"] == _sha(b"# content")
    assert body["sealed"] is True

    again = finalize_version(client, tenant_a, asset["id"], version_id)
    assert again.status_code == 200, again.text
    assert again.json()["sha256"] == body["sha256"]


@requires_postgres
def test_finalize_without_bytes_is_not_ready(client: TestClient, tenant_a: Tenant) -> None:
    asset = create_asset(client, tenant_a)
    begun = begin_version(client, tenant_a, asset["id"])

    response = finalize_version(client, tenant_a, asset["id"], begun["version"]["id"])
    assert response.status_code == 422, response.text
    assert response.json()["code"] == "NOT_READY"


@requires_postgres
def test_finalize_detects_bytes_changed_after_upload(
    client: TestClient, tenant_a: Tenant
) -> None:
    from prodavan.infrastructure.files.manager import ensure_file_store

    asset = create_asset(client, tenant_a)
    begun = begin_version(client, tenant_a, asset["id"])
    version_id = begun["version"]["id"]
    storage_key = begun["version"]["storage_key"]
    uploaded = upload_version(client, tenant_a, asset["id"], version_id, b"original")
    assert uploaded.status_code == 200, uploaded.text

    detail = client.get(
        f"{CONTENT}/assets/{asset['id']}/versions/{version_id}", headers=tenant_a.headers
    )
    assert detail.status_code == 200, detail.text
    assert "storage_key" not in detail.json(), "version detail must not expose storage keys"

    # Overwrite the stored object behind the API's back, then re-finalize.
    ensure_file_store().put_bytes_sync(storage_key, b"tampered")
    rejected = finalize_version(client, tenant_a, asset["id"], version_id)
    assert rejected.status_code == 422, rejected.text
    assert rejected.json()["code"] == "CONTENT_DIGEST_MISMATCH"


@requires_postgres
def test_version_history_is_ordered_and_paginated(
    client: TestClient, tenant_a: Tenant
) -> None:
    asset = create_asset(client, tenant_a)
    for payload in (b"v1", b"v2", b"v3"):
        upload_sealed_version(client, tenant_a, asset["id"], payload)

    first = client.get(
        f"{CONTENT}/assets/{asset['id']}/versions",
        headers=tenant_a.headers,
        params={"limit": 2},
    )
    assert first.status_code == 200, first.text
    body = first.json()
    assert [item["version"] for item in body["items"]] == [3, 2]
    assert body["has_more"] is True
    assert body["next_cursor"]

    second = client.get(
        f"{CONTENT}/assets/{asset['id']}/versions",
        headers=tenant_a.headers,
        params={"limit": 2, "cursor": body["next_cursor"]},
    )
    assert second.status_code == 200, second.text
    tail = second.json()
    assert [item["version"] for item in tail["items"]] == [1]
    assert tail["has_more"] is False
    assert tail["next_cursor"] is None
    for item in body["items"] + tail["items"]:
        assert "storage_key" not in item


@requires_postgres
def test_version_of_another_asset_is_404(client: TestClient, tenant_a: Tenant) -> None:
    first = create_asset(client, tenant_a)
    second = create_asset(client, tenant_a)
    sealed = upload_sealed_version(client, tenant_a, first["id"], b"x")

    response = client.get(
        f"{CONTENT}/assets/{second['id']}/versions/{sealed['id']}", headers=tenant_a.headers
    )
    assert response.status_code == 404, response.text

    upload = upload_version(client, tenant_a, second["id"], sealed["id"], b"y")
    assert upload.status_code == 404, upload.text


@requires_postgres
def test_cross_tenant_version_access_is_404(
    client: TestClient, tenant_a: Tenant, tenant_b: Tenant
) -> None:
    asset = create_asset(client, tenant_a)
    sealed = upload_sealed_version(client, tenant_a, asset["id"], b"private")

    read = client.get(
        f"{CONTENT}/assets/{asset['id']}/versions/{sealed['id']}", headers=tenant_b.headers
    )
    assert read.status_code == 404, read.text

    upload = upload_version(client, tenant_b, asset["id"], sealed["id"], b"x")
    assert upload.status_code == 404, upload.text

    finalize = finalize_version(client, tenant_b, asset["id"], sealed["id"])
    assert finalize.status_code == 404, finalize.text

    started = client.post(
        f"{CONTENT}/assets/{asset['id']}/versions",
        headers=tenant_b.headers,
        json={"mime": "text/plain"},
    )
    assert started.status_code == 404, started.text


@requires_postgres
def test_download_requires_a_sealed_version(client: TestClient, tenant_a: Tenant) -> None:
    asset = create_asset(client, tenant_a)
    begun = begin_version(client, tenant_a, asset["id"])

    response = client.get(
        f"{CONTENT}/assets/{asset['id']}/download",
        headers=tenant_a.headers,
        params={"blob_version_id": begun["version"]["id"], "proxy": "true"},
    )
    assert response.status_code == 422, response.text
    assert response.json()["code"] == "NOT_READY"


@requires_postgres
def test_download_streams_exact_version_with_headers(
    client: TestClient, tenant_a: Tenant
) -> None:
    asset = create_asset(client, tenant_a, title="report.xlsx")
    first = upload_sealed_version(client, tenant_a, asset["id"], b"first-bytes")
    upload_sealed_version(client, tenant_a, asset["id"], b"second-bytes")

    response = client.get(
        f"{CONTENT}/assets/{asset['id']}/download",
        headers=tenant_a.headers,
        params={"blob_version_id": first["id"], "proxy": "true"},
    )
    assert response.status_code == 200, response.text
    assert response.content == b"first-bytes"
    assert response.headers["X-Content-SHA256"] == _sha(b"first-bytes")
    assert response.headers["X-Content-Size"] == str(len(b"first-bytes"))
    assert "report.xlsx" in response.headers["Content-Disposition"]
    assert response.headers["Content-Disposition"].startswith("attachment;")


@requires_postgres
def test_download_defaults_to_the_latest_sealed_version(
    client: TestClient, tenant_a: Tenant
) -> None:
    asset = create_asset(client, tenant_a)
    upload_sealed_version(client, tenant_a, asset["id"], b"old")
    upload_sealed_version(client, tenant_a, asset["id"], b"new")

    response = client.get(
        f"{CONTENT}/assets/{asset['id']}/download",
        headers=tenant_a.headers,
        params={"proxy": "true"},
    )
    assert response.status_code == 200, response.text
    assert response.content == b"new"
