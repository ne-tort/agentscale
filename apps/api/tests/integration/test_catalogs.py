"""M04 catalog + M02 catalog search tests."""

import pytest
from httpx import AsyncClient

from tests.conftest import requires_postgres
from tests.helpers import active_cabinet_headers, register_user


async def _project(client: AsyncClient, headers: dict, suffix: str) -> str:
    created = await client.post(
        "/api/v1/projects",
        headers=headers,
        json={"slug": f"cat-{suffix}", "display_name": "Cat project"},
    )
    assert created.status_code == 201, created.text
    project_id = created.json()["id"]
    opened = await client.post(f"/api/v1/projects/{project_id}/open", headers=headers)
    assert opened.status_code == 200
    return project_id


@requires_postgres
@pytest.mark.asyncio
async def test_catalog_upload_feeds_search(client: AsyncClient, unique_suffix: str) -> None:
    reg = await register_user(client, unique_suffix, prefix="cat")
    cabinet_id, headers = await active_cabinet_headers(client, reg, unique_suffix)
    project_id = await _project(client, headers, unique_suffix)

    catalog_csv = "part_number,title,price,stock\n910-001793,Mouse,890,in_stock\nX-SKIP,Skip,10,on_order\n"
    upload = await client.post(
        f"/api/v1/cabinets/{cabinet_id}/catalogs/upload",
        headers=headers,
        files={"file": ("distrib.csv", catalog_csv.encode("utf-8"), "text/csv")},
        data={"slug": "distrib-main", "display_name": "Дистрибьютор", "trusted_seller": "true"},
    )
    assert upload.status_code == 202, upload.text
    assert upload.json()["status"] == "ready"
    assert upload.json()["stats"]["rows"] == 1

    listed = await client.get(f"/api/v1/cabinets/{cabinet_id}/catalogs", headers=headers)
    assert listed.status_code == 200
    assert listed.json()["items"][0]["trusted_seller"] is True

    spec = "name,qty,part_number\nMouse Logitech,10,910-001793\n"
    run_up = await client.post(
        f"/api/v1/projects/{project_id}/inbox/upload",
        headers=headers,
        files={"file": ("spec.csv", spec.encode("utf-8"), "text/csv")},
        data={"auto_run": "true"},
    )
    run_id = run_up.json()["run_id"]
    for phase in ("classify", "search"):
        step = await client.post(
            f"/api/v1/projects/{project_id}/runs/{run_id}/advance",
            headers=headers,
            json={"target_phase": phase},
        )
        assert step.status_code == 202, step.text

    offers = await client.get(
        f"/api/v1/projects/{project_id}/runs/{run_id}/offers", headers=headers
    )
    assert offers.status_code == 200
    items = offers.json()["items"]
    assert len(items) == 1
    assert items[0]["price"] == 890
    assert items[0]["source"] == "catalog"
    assert items[0]["in_stock"] is True


@requires_postgres
@pytest.mark.asyncio
async def test_neg_cat_001_system_db_not_deletable(
    client: AsyncClient, unique_suffix: str
) -> None:
    """NEG-CAT-001: DELETE s4b-cache is always 403."""
    reg = await register_user(client, unique_suffix, prefix="cat-sys")
    headers = {"Authorization": f"Bearer {reg['access_token']}"}
    resp = await client.delete("/api/v1/system-databases/s4b-cache", headers=headers)
    assert resp.status_code == 403
    assert resp.json()["code"] == "SYSTEM_DATABASE_NON_DELETABLE"


@requires_postgres
@pytest.mark.asyncio
async def test_neg_cat_002_generic_has_no_s4b_system_db(
    client: AsyncClient, unique_suffix: str
) -> None:
    """NEG-CAT-002: generic cabinet system-databases empty."""
    reg = await register_user(client, unique_suffix, prefix="cat-gen")
    cabinet_id, headers = await active_cabinet_headers(
        client, reg, unique_suffix, profile_id="generic-assistant"
    )
    dbs = await client.get(
        f"/api/v1/cabinets/{cabinet_id}/system-databases", headers=headers
    )
    assert dbs.status_code == 200
    assert dbs.json()["items"] == []


@requires_postgres
@pytest.mark.asyncio
async def test_neg_cat_003_vault_secret_not_in_api(
    client: AsyncClient, unique_suffix: str
) -> None:
    """NEG-CAT-003: password never returned."""
    reg = await register_user(client, unique_suffix, prefix="cat-vault")
    headers = {"Authorization": f"Bearer {reg['access_token']}"}
    put = await client.put(
        "/api/v1/tenant/s4b-credentials",
        headers=headers,
        json={"username": "buyer@acme.ru", "password": "super-secret"},
    )
    assert put.status_code == 200
    assert "password" not in put.json()
    assert put.json()["state"] != "credentials_valid"
    assert put.json()["state"] == "credentials_invalid"

    status = await client.get("/api/v1/tenant/s4b-credentials/status", headers=headers)
    body = status.json()
    assert "password" not in body
    assert "super-secret" not in str(body)
    assert body["s4b_username_hint"] == "bu***@acme.ru"


@requires_postgres
@pytest.mark.asyncio
async def test_s4b_rate_limited_ping_still_validates_account(
    client: AsyncClient, unique_suffix: str, monkeypatch
) -> None:
    class _RateLimited:
        def ping(self, username: str, password: str) -> dict:
            return {"ok": False, "error_code": "rate_limited", "error": "Too frequently"}

        def search_by_part_numbers(self, *args, **kwargs) -> dict:
            return {"ok": False, "error_code": "rate_limited"}

    monkeypatch.setattr("prodavan.application.integrations.s4b_runtime._gateway", _RateLimited())
    reg = await register_user(client, unique_suffix, prefix="s4b-rl")
    headers = {"Authorization": f"Bearer {reg['access_token']}"}
    put = await client.put(
        "/api/v1/tenant/s4b-credentials",
        headers=headers,
        json={"username": "buyer@acme.ru", "password": "secret"},
    )
    assert put.status_code == 200
    assert put.json()["state"] == "credentials_valid"
    assert "secret" not in str(put.json())


class _ValidS4B:
    def ping(self, username: str, password: str) -> dict:
        return {"ok": True, "auth_ok": True}

    def search_by_part_numbers(self, username: str, password: str, part_numbers: list[str]) -> dict:
        from prodavan.application.integrations.s4b_parse import parse_response

        raw = {
            "results": [
                {
                    "in": "910-001793",
                    "listStock": {
                        "rows": [
                            [
                                "1",
                                "910-001793",
                                "Mouse trusted",
                                "5",
                                "5",
                                "1-2 дн",
                                "x",
                                "1200",
                                "Merlion",
                                "Logitech",
                            ],
                            [
                                "9",
                                "910-001793",
                                "Mouse cheap untrusted",
                                "8",
                                "8",
                                "1 дн",
                                "x",
                                "100",
                                "RandomCo",
                                "Logitech",
                            ],
                            [
                                "3",
                                "910-001793",
                                "Mouse backorder",
                                "2",
                                "2",
                                "под заказ",
                                "x",
                                "50",
                                "Merlion",
                                "Logitech",
                            ],
                        ]
                    },
                    "listNoStock": {
                        "rows": [
                            ["2", "910-001793", "Mouse OO", "1", "1", "Merlion", "X"],
                        ]
                    },
                }
            ]
        }
        items, _ = parse_response(raw)
        return {"ok": True, "items": items}


@requires_postgres
@pytest.mark.asyncio
async def test_s4b_live_search_keeps_in_stock_and_ranks_trusted(
    client: AsyncClient, unique_suffix: str, monkeypatch
) -> None:
    monkeypatch.setattr("prodavan.application.integrations.s4b_runtime._gateway", _ValidS4B())
    reg = await register_user(client, unique_suffix, prefix="s4b-live")
    _, headers = await active_cabinet_headers(client, reg, unique_suffix)
    project_id = await _project(client, headers, unique_suffix + "s")

    put = await client.put(
        "/api/v1/tenant/s4b-credentials",
        headers=headers,
        json={"username": "buyer@acme.ru", "password": "ok-secret"},
    )
    assert put.status_code == 200, put.text
    assert put.json()["state"] == "credentials_valid"
    assert "ok-secret" not in str(put.json())

    spec = "name,qty,part_number\nMouse Logitech,10,910-001793\n"
    run_up = await client.post(
        f"/api/v1/projects/{project_id}/inbox/upload",
        headers=headers,
        files={"file": ("spec.csv", spec.encode("utf-8"), "text/csv")},
        data={"auto_run": "true"},
    )
    run_id = run_up.json()["run_id"]
    for phase in ("classify", "search", "rank"):
        step = await client.post(
            f"/api/v1/projects/{project_id}/runs/{run_id}/advance",
            headers=headers,
            json={"target_phase": phase},
        )
        assert step.status_code == 202, step.text

    offers = await client.get(
        f"/api/v1/projects/{project_id}/runs/{run_id}/offers", headers=headers
    )
    items = offers.json()["items"]
    prices = sorted(o["price"] for o in items)
    assert prices == [100, 1200]
    assert all(o["in_stock"] is True for o in items)
    assert all(o["source"] == "s4b" for o in items)
    trusted = next(o for o in items if o["trusted_seller"])
    cheap = next(o for o in items if not o["trusted_seller"])
    assert trusted["price"] == 1200
    assert cheap["price"] == 100

