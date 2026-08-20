"""Spec run orchestration (M02). Disk artifacts + phase guards; no invented prices."""

from __future__ import annotations

import json
import secrets
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from prodavan.application.pipeline.classify import classify_rows
from prodavan.application.pipeline.ingest import extracted_markdown, ingest_rows
from prodavan.application.catalogs.s4b_search import search_lineitems_in_s4b
from prodavan.application.catalogs.search import rank_selections, search_lineitems_in_catalogs
from prodavan.application.pipeline.kp_export import (
    TEMPLATE_VERSION,
    build_kp_rows,
    timestamp_stamp,
    write_kp_workbook,
)
from prodavan.application.pipeline.variants import import_run_to_sqlite
from prodavan.application.services.cabinet_service import CabinetError, get_cabinet
from prodavan.application.services.project_service import ProjectError, get_project
from prodavan.domain.pipeline import (
    artifact_required_for,
    can_advance,
    can_finalize,
    next_phase,
)
from prodavan.infrastructure.storage import run_storage as store
from prodavan.infrastructure.storage.project_storage import project_root
from prodavan.infrastructure.storage.run_storage import RunStorageError


class PipelineError(Exception):
    def __init__(self, code: str, message: str, status: int = 400) -> None:
        self.code = code
        self.message = message
        self.status = status
        super().__init__(message)


def _new_run_id() -> str:
    return f"01{secrets.token_hex(10)}"


def _wrap_storage(exc: RunStorageError) -> PipelineError:
    status = 404 if exc.code in {"RUN_NOT_FOUND", "INBOX_FILE_NOT_FOUND"} else 400
    return PipelineError(exc.code, exc.message, status)


async def _project_or_raise(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    cabinet_id: uuid.UUID,
    project_id: str,
):
    try:
        project = await get_project(
            session,
            tenant_id=tenant_id,
            user_id=user_id,
            cabinet_id=cabinet_id,
            project_id=project_id,
        )
    except ProjectError as exc:
        raise PipelineError(exc.code, exc.message, exc.status) from exc
    if project.status == "archived":
        raise PipelineError("PROJECT_ARCHIVED", "Cannot run pipeline on archived project", 409)
    return project


def _s4b_enabled(capabilities: dict) -> bool:
    return bool(capabilities.get("integrations", {}).get("s4b", {}).get("enabled"))


def _equipment_enabled(capabilities: dict) -> bool:
    return bool(capabilities.get("modules", {}).get("equipment_cards", {}).get("enabled"))


def _kp_enabled(capabilities: dict) -> bool:
    return bool(capabilities.get("modules", {}).get("specs_kp", {}).get("enabled"))


async def upload_inbox(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    cabinet_id: uuid.UUID,
    project_id: str,
    filename: str,
    data: bytes,
    auto_run: bool,
) -> dict:
    project = await _project_or_raise(
        session, tenant_id=tenant_id, user_id=user_id, cabinet_id=cabinet_id, project_id=project_id
    )
    try:
        meta = store.write_inbox_file(tenant_id, cabinet_id, project_id, filename, data)
    except RunStorageError as exc:
        raise _wrap_storage(exc) from exc

    extracted_name = None
    inbox_path = meta["path"]
    ingest_preview = ingest_rows(inbox_path)
    md_name = f"{meta['filename']}.extracted.md"
    md = extracted_markdown(meta["filename"], ingest_preview)
    store.write_inbox_file(
        tenant_id, cabinet_id, project_id, md_name, md.encode("utf-8")
    )
    extracted_name = md_name

    result = {
        "filename": meta["filename"],
        "size_bytes": meta["size_bytes"],
        "sha256": meta["sha256"],
        "extracted_md": extracted_name,
        "run_id": None,
        "run_phase": None,
    }
    if auto_run:
        created = await create_run(
            session,
            tenant_id=tenant_id,
            user_id=user_id,
            cabinet_id=cabinet_id,
            project_id=project_id,
            input_filename=meta["filename"],
        )
        result["run_id"] = created["run_id"]
        result["run_phase"] = created["phase"]
    return result


async def create_run(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    cabinet_id: uuid.UUID,
    project_id: str,
    input_filename: str,
) -> dict:
    project = await _project_or_raise(
        session, tenant_id=tenant_id, user_id=user_id, cabinet_id=cabinet_id, project_id=project_id
    )
    try:
        cabinet = await get_cabinet(
            session, tenant_id=tenant_id, user_id=user_id, cabinet_id=cabinet_id
        )
    except CabinetError as exc:
        raise PipelineError(exc.code, exc.message, exc.status) from exc
    caps = cabinet.capabilities or {}
    snapshot = {
        "s4b": _s4b_enabled(caps),
        "equipment_cards": _equipment_enabled(caps),
        "specs_kp": _kp_enabled(caps),
    }
    run_id = _new_run_id()
    try:
        status = store.init_run_dir(
            tenant_id,
            cabinet_id,
            project_id,
            run_id,
            workspace_key=project.workspace_key,
            input_filename=input_filename,
            capabilities_snapshot=snapshot,
        )
    except RunStorageError as exc:
        raise _wrap_storage(exc) from exc

    _run_ingest(tenant_id, cabinet_id, project_id, run_id, status)
    return {
        "run_id": run_id,
        "workspace_key": project.workspace_key,
        "phase": store.load_status(tenant_id, cabinet_id, project_id, run_id)["phase"],
        "status_uri": (
            f"prodavan://storage/cabinets/{tenant_id}/{cabinet_id}/projects/{project_id}/runs/{run_id}/status.json"
        ),
    }


def _run_ingest(tenant_id, cabinet_id, project_id, run_id, status: dict) -> None:
    filename = status["input_file"]
    input_path = store.input_file_path(tenant_id, cabinet_id, project_id, run_id, filename)
    ingest = ingest_rows(input_path)
    rows_payload = {"run_id": run_id, **ingest}
    store.write_json_artifact(tenant_id, cabinet_id, project_id, run_id, "rows.json", rows_payload)
    status["stats"]["rows"] = len(ingest["rows"])
    status["phase"] = "ingest"
    status["phase_status"] = "completed"
    status["phase_history"].append(
        {"phase": "ingest", "status": "completed", "parser": ingest.get("parser")}
    )
    store.write_status(tenant_id, cabinet_id, project_id, run_id, status)


def _run_classify(tenant_id, cabinet_id, project_id, run_id, status: dict) -> None:
    rows_doc = store.read_json_artifact(tenant_id, cabinet_id, project_id, run_id, "rows.json") or {}
    classified = classify_rows(rows_doc.get("rows") or [])
    store.write_json_artifact(
        tenant_id, cabinet_id, project_id, run_id, "lineitems.json", classified
    )
    store.write_json_artifact(
        tenant_id,
        cabinet_id,
        project_id,
        run_id,
        "needs-review.json",
        {"line_ids": [i["line_id"] for i in classified["items"] if i.get("needs_review")]},
    )
    status["stats"]["line_items"] = len(classified["items"])
    status["stats"]["needs_review"] = classified["needs_review"]
    status["phase"] = "classify"
    status["phase_status"] = "completed"
    status["phase_history"].append({"phase": "classify", "status": "completed"})
    store.write_status(tenant_id, cabinet_id, project_id, run_id, status)


def _run_search(tenant_id, cabinet_id, project_id, run_id, status: dict) -> None:
    s4b = bool(status.get("capabilities_snapshot", {}).get("s4b"))
    lineitems = (
        store.read_json_artifact(tenant_id, cabinet_id, project_id, run_id, "lineitems.json") or {}
    ).get("items") or []
    offers, logs = search_lineitems_in_catalogs(tenant_id, cabinet_id, lineitems)
    s4b_offers, s4b_logs = search_lineitems_in_s4b(
        tenant_id,
        lineitems,
        s4b_enabled=s4b,
        start_seq=len(offers),
    )
    offers.extend(s4b_offers)
    logs.extend(s4b_logs)
    store.write_json_artifact(
        tenant_id,
        cabinet_id,
        project_id,
        run_id,
        "offers.json",
        {
            "offers": offers,
            "sources_log_ref": "sources.log",
            "s4b_eligible": s4b,
        },
    )
    for line in logs:
        store.append_sources_log(tenant_id, cabinet_id, project_id, run_id, line)
    store.append_sources_log(
        tenant_id, cabinet_id, project_id, run_id, "web skipped=allowlist_not_queried"
    )
    status["stats"]["offers"] = len(offers)
    status["phase"] = "search"
    status["phase_status"] = "completed"
    status["phase_history"].append({"phase": "search", "status": "completed"})
    store.write_status(tenant_id, cabinet_id, project_id, run_id, status)


def _run_rank(tenant_id, cabinet_id, project_id, run_id, status: dict) -> None:
    lineitems = (
        store.read_json_artifact(tenant_id, cabinet_id, project_id, run_id, "lineitems.json") or {}
    ).get("items") or []
    offers = (
        store.read_json_artifact(tenant_id, cabinet_id, project_id, run_id, "offers.json") or {}
    ).get("offers") or []
    store.write_json_artifact(
        tenant_id,
        cabinet_id,
        project_id,
        run_id,
        "selection.json",
        rank_selections(lineitems, offers),
    )
    status["phase"] = "rank"
    status["phase_status"] = "completed"
    status["phase_history"].append({"phase": "rank", "status": "completed"})
    store.write_status(tenant_id, cabinet_id, project_id, run_id, status)


def _run_variants(tenant_id, cabinet_id, project_id, run_id, status: dict) -> None:
    lineitems = (
        store.read_json_artifact(tenant_id, cabinet_id, project_id, run_id, "lineitems.json") or {}
    )
    offers = store.read_json_artifact(tenant_id, cabinet_id, project_id, run_id, "offers.json") or {}
    selection = (
        store.read_json_artifact(tenant_id, cabinet_id, project_id, run_id, "selection.json") or {}
    )
    import_run_to_sqlite(
        tenant_id=tenant_id,
        cabinet_id=cabinet_id,
        project_id=project_id,
        run_id=run_id,
        lineitems=lineitems.get("items") or [],
        offers=offers.get("offers") or [],
        selections=selection.get("selections") or [],
    )
    status["phase"] = "variants"
    status["phase_status"] = "completed"
    status["phase_history"].append({"phase": "variants", "status": "completed"})
    store.write_status(tenant_id, cabinet_id, project_id, run_id, status)


def _run_review(tenant_id, cabinet_id, project_id, run_id, status: dict) -> None:
    status["phase"] = "review"
    status["phase_status"] = "completed"
    status["phase_history"].append({"phase": "review", "status": "completed"})
    store.write_status(tenant_id, cabinet_id, project_id, run_id, status)


_HANDLERS = {
    "classify": _run_classify,
    "search": _run_search,
    "rank": _run_rank,
    "variants": _run_variants,
    "review": _run_review,
}


async def advance_run(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    cabinet_id: uuid.UUID,
    project_id: str,
    run_id: str,
    target_phase: str | None,
) -> dict:
    await _project_or_raise(
        session, tenant_id=tenant_id, user_id=user_id, cabinet_id=cabinet_id, project_id=project_id
    )
    try:
        status = store.load_status(tenant_id, cabinet_id, project_id, run_id)
    except RunStorageError as exc:
        raise _wrap_storage(exc) from exc

    current = status["phase"]
    target = target_phase or next_phase(current)
    if target is None:
        raise PipelineError("PHASE_GUARD_FAILED", "No next phase", 409)
    if not can_advance(current=current, target=target):
        raise PipelineError(
            "PHASE_GUARD_FAILED",
            f"Cannot advance {current} → {target}",
            409,
        )
    required = artifact_required_for(target)
    if required and not store.artifact_exists(tenant_id, cabinet_id, project_id, run_id, required):
        raise PipelineError(
            "PHASE_GUARD_FAILED",
            f"Missing artifact {required} for {target}",
            409,
        )
    handler = _HANDLERS.get(target)
    if handler is None:
        raise PipelineError("PHASE_GUARD_FAILED", f"Unsupported target {target}", 409)
    handler(tenant_id, cabinet_id, project_id, run_id, status)
    return {
        "job_id": f"job_{run_id}_{target}",
        "from_phase": current,
        "to_phase": target,
    }


async def finalize_run(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    cabinet_id: uuid.UUID,
    project_id: str,
    run_id: str,
    confirmed: bool,
    operator_note: str | None,
) -> dict:
    await _project_or_raise(
        session, tenant_id=tenant_id, user_id=user_id, cabinet_id=cabinet_id, project_id=project_id
    )
    if not confirmed:
        raise PipelineError("CONFIRM_REQUIRED", "confirmed=true required", 400)
    try:
        status = store.load_status(tenant_id, cabinet_id, project_id, run_id)
    except RunStorageError as exc:
        raise _wrap_storage(exc) from exc
    if not can_finalize(status["phase"]):
        raise PipelineError("PHASE_GUARD_FAILED", "final only from review + operator OK", 409)
    status["phase"] = "final"
    status["phase_status"] = "completed"
    status["operator_note"] = operator_note
    status["phase_history"].append({"phase": "final", "status": "completed"})
    store.write_status(tenant_id, cabinet_id, project_id, run_id, status)
    return status


def describe_run(tenant_id, cabinet_id, project_id, run_id) -> dict:
    try:
        status = store.load_status(tenant_id, cabinet_id, project_id, run_id)
    except RunStorageError as exc:
        raise _wrap_storage(exc) from exc
    artifacts = {}
    for name, key in (
        ("rows.json", "rows"),
        ("lineitems.json", "lineitems"),
        ("offers.json", "offers"),
        ("selection.json", "selection"),
    ):
        artifacts[key] = (
            f"runs/{run_id}/{name}"
            if store.artifact_exists(tenant_id, cabinet_id, project_id, run_id, name)
            else None
        )
    return {
        "run_id": run_id,
        "phase": status["phase"],
        "phase_status": status.get("phase_status"),
        "artifacts": artifacts,
        "stats": status.get("stats", {}),
        "capabilities_snapshot": status.get("capabilities_snapshot", {}),
    }


def list_runs(tenant_id, cabinet_id, project_id) -> dict:
    items = []
    for run_id in store.list_run_ids(tenant_id, cabinet_id, project_id):
        try:
            items.append(describe_run(tenant_id, cabinet_id, project_id, run_id))
        except PipelineError:
            items.append({"run_id": run_id, "phase": "unknown", "phase_status": None})
    return {"items": items, "count": len(items)}


def list_lineitems(tenant_id, cabinet_id, project_id, run_id) -> dict:
    doc = store.read_json_artifact(tenant_id, cabinet_id, project_id, run_id, "lineitems.json")
    if doc is None:
        raise PipelineError("PHASE_GUARD_FAILED", "lineitems.json missing", 409)
    return {"items": doc.get("items") or []}


def list_offers(tenant_id, cabinet_id, project_id, run_id) -> dict:
    doc = store.read_json_artifact(tenant_id, cabinet_id, project_id, run_id, "offers.json")
    if doc is None:
        raise PipelineError("PHASE_GUARD_FAILED", "offers.json missing", 409)
    offers = [o for o in (doc.get("offers") or []) if o.get("in_stock") is not False]
    return {"items": offers}


async def export_kp(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    cabinet_id: uuid.UUID,
    project_id: str,
    run_id: str,
    include_alternatives: bool,
) -> dict:
    await _project_or_raise(
        session, tenant_id=tenant_id, user_id=user_id, cabinet_id=cabinet_id, project_id=project_id
    )
    try:
        status = store.load_status(tenant_id, cabinet_id, project_id, run_id)
    except RunStorageError as exc:
        raise _wrap_storage(exc) from exc
    if not status.get("capabilities_snapshot", {}).get("specs_kp"):
        raise PipelineError("CAPABILITY_MISSING", "KP export requires specs_kp", 403)
    if status.get("phase") not in {"review", "final"}:
        raise PipelineError("PHASE_GUARD_FAILED", "KP export after review or final only", 409)

    lineitems = list_lineitems(tenant_id, cabinet_id, project_id, run_id)["items"]
    offers = (
        store.read_json_artifact(tenant_id, cabinet_id, project_id, run_id, "offers.json") or {}
    ).get("offers") or []
    selections = (
        store.read_json_artifact(tenant_id, cabinet_id, project_id, run_id, "selection.json") or {}
    ).get("selections") or []
    rows = build_kp_rows(
        lineitems, offers, selections, include_alternatives=include_alternatives
    )
    stamp = timestamp_stamp()
    filename = f"kp-{run_id}-{stamp}.xlsx"
    export_dir = project_root(tenant_id, cabinet_id, project_id) / "export"
    xlsx_path = export_dir / filename
    write_kp_workbook(xlsx_path, rows)
    meta = {
        "run_id": run_id,
        "template_version": TEMPLATE_VERSION,
        "generated_at": stamp,
        "operator_finalized": status.get("phase") == "final",
        "lines_filled": sum(1 for row in rows if row[7] is not None),
        "lines_review": sum(1 for row in rows if row[10] is True and row[4] in {"", "primary"}),
    }
    meta_path = export_dir / f"kp-{run_id}-{stamp}.meta.json"
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    return {
        "export_path": f"export/{filename}",
        "download_url": f"/api/v1/projects/{project_id}/export/{filename}",
        "lines_filled": meta["lines_filled"],
        "lines_review": meta["lines_review"],
    }


def resolve_export_file(
    tenant_id: uuid.UUID,
    cabinet_id: uuid.UUID,
    project_id: str,
    filename: str,
):
    from prodavan.infrastructure.storage.run_storage import sanitize_filename

    safe = sanitize_filename(filename)
    path = project_root(tenant_id, cabinet_id, project_id) / "export" / safe
    if not path.is_file():
        raise PipelineError("EXPORT_NOT_FOUND", "Export file not found", 404)
    return path
