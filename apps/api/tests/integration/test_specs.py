"""M02 spec pipeline integration tests."""

import pytest
from httpx import AsyncClient

from tests.conftest import requires_postgres
from tests.helpers import active_cabinet_headers, register_user


async def _open_project(client: AsyncClient, headers: dict, suffix: str) -> str:
    created = await client.post(
        "/api/v1/projects",
        headers=headers,
        json={"slug": f"spec-{suffix}", "display_name": "Spec project"},
    )
    assert created.status_code == 201, created.text
    project_id = created.json()["id"]
    opened = await client.post(f"/api/v1/projects/{project_id}/open", headers=headers)
    assert opened.status_code == 200
    return project_id


@requires_postgres
@pytest.mark.asyncio
async def test_csv_upload_ingest_and_advance(client: AsyncClient, unique_suffix: str) -> None:
    reg = await register_user(client, unique_suffix, prefix="skp")
    _, headers = await active_cabinet_headers(client, reg, unique_suffix)
    project_id = await _open_project(client, headers, unique_suffix)

    csv_body = "name,qty,part_number\nMouse Logitech,10,910-001793\n"
    upload = await client.post(
        f"/api/v1/projects/{project_id}/inbox/upload",
        headers=headers,
        files={"file": ("spec-alpha.csv", csv_body.encode("utf-8"), "text/csv")},
        data={"auto_run": "true"},
    )
    assert upload.status_code == 201, upload.text
    data = upload.json()
    assert data["extracted_md"] == "spec-alpha.csv.extracted.md"
    run_id = data["run_id"]
    assert data["run_phase"] == "ingest"

    run = await client.get(f"/api/v1/projects/{project_id}/runs/{run_id}", headers=headers)
    assert run.status_code == 200
    assert run.json()["stats"]["rows"] == 1
    assert run.json()["artifacts"]["rows"]

    classify = await client.post(
        f"/api/v1/projects/{project_id}/runs/{run_id}/advance",
        headers=headers,
        json={"target_phase": "classify"},
    )
    assert classify.status_code == 202, classify.text

    items = await client.get(
        f"/api/v1/projects/{project_id}/runs/{run_id}/lineitems", headers=headers
    )
    assert items.status_code == 200
    line = items.json()["items"][0]
    assert line["part_number"] == "910-001793"
    assert line["raw_text"]

    skip_rank = await client.post(
        f"/api/v1/projects/{project_id}/runs/{run_id}/advance",
        headers=headers,
        json={"target_phase": "rank"},
    )
    assert skip_rank.status_code == 409
    assert skip_rank.json()["code"] == "PHASE_GUARD_FAILED"

    for phase in ("search", "rank", "variants", "review"):
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
    assert offers.json()["items"] == []

    done = await client.post(
        f"/api/v1/projects/{project_id}/runs/{run_id}/finalize",
        headers=headers,
        json={"confirmed": True, "operator_note": "OK"},
    )
    assert done.status_code == 200
    assert done.json()["phase"] == "final"


@requires_postgres
@pytest.mark.asyncio
async def test_neg_skp_003_finalize_without_review(
    client: AsyncClient, unique_suffix: str
) -> None:
    """NEG-SKP-003: final without operator review phase."""
    reg = await register_user(client, unique_suffix, prefix="skp-fin")
    _, headers = await active_cabinet_headers(client, reg, unique_suffix)
    project_id = await _open_project(client, headers, unique_suffix)

    upload = await client.post(
        f"/api/v1/projects/{project_id}/inbox/upload",
        headers=headers,
        files={"file": ("spec.txt", b"Notebook ACER 16GB\n", "text/plain")},
        data={"auto_run": "true"},
    )
    run_id = upload.json()["run_id"]
    finalize = await client.post(
        f"/api/v1/projects/{project_id}/runs/{run_id}/finalize",
        headers=headers,
        json={"confirmed": True},
    )
    assert finalize.status_code == 409
    assert finalize.json()["code"] == "PHASE_GUARD_FAILED"


@requires_postgres
@pytest.mark.asyncio
async def test_xlsx_does_not_invent_rows(client: AsyncClient, unique_suffix: str) -> None:
    reg = await register_user(client, unique_suffix, prefix="skp-xlsx")
    _, headers = await active_cabinet_headers(client, reg, unique_suffix)
    project_id = await _open_project(client, headers, unique_suffix)

    upload = await client.post(
        f"/api/v1/projects/{project_id}/inbox/upload",
        headers=headers,
        files={"file": ("spec.xlsx", b"PK\x03\x04not-a-real-xlsx", "application/vnd.ms-excel")},
        data={"auto_run": "true"},
    )
    assert upload.status_code == 201, upload.text
    run_id = upload.json()["run_id"]
    run = await client.get(f"/api/v1/projects/{project_id}/runs/{run_id}", headers=headers)
    assert run.json()["stats"]["rows"] == 0
