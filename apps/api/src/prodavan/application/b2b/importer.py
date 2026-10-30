"""Periodic B2B-Center -> Agentscale tender importer.

Writes directly into the existing `tenders` module table (mod_equipment) — no
webhooks, no external `b2b-crawler` compose stack. Deterministic `row_id` +
content hash make the importer idempotent: an unchanged card is skipped, a
changed description/positions update the same row while preserving the user's
workflow status, comments and imported chat links.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from prodavan.application.modules.module_instance_service import (
    OWNER_CABINET,
    OWNER_PROJECT,
    ModuleInstanceService,
)
from prodavan.config.settings import Settings
from prodavan.config.settings import settings as global_settings
from prodavan.domain.tenders import lifecycle_from_deadline
from prodavan.infrastructure.persistence.models.projects import ProjectRow

from .client import B2BCenterClient, B2BError
from .dto import B2BTradeRef
from .filters import B2BCategoryFilter
from .mapping import card_to_tender_body, changed_fields, stable_row_id

MODULE_ID = "mod_equipment"
TENDERS_TABLE = "tenders"
STATE_TABLE = "b2b_import_state"
STATE_ROW_ID = "default"
_log = logging.getLogger(__name__)


class B2BImportService:
    def __init__(
        self,
        session: AsyncSession,
        *,
        settings: Settings | None = None,
        client_factory: Callable[[], B2BCenterClient] | None = None,
    ) -> None:
        self._session = session
        self._settings = settings or global_settings
        self._client_factory = client_factory

    async def run(
        self,
        *,
        project_id: str | None = None,
        cabinet_id: str | None = None,
        force: bool = False,
    ) -> dict[str, Any]:
        cfg = self._settings
        if not cfg.b2b_center_enabled:
            return {"ok": False, "skipped": "b2b_center_disabled"}
        if not cfg.b2b_center_login or not cfg.b2b_center_password:
            return {"ok": False, "skipped": "b2b_center_credentials_missing"}

        project_id = (project_id or cfg.b2b_center_project_id or "").strip() or None
        cabinet_id = (cabinet_id or cfg.b2b_center_cabinet_id or "").strip() or None
        project: ProjectRow | None = None
        if project_id:
            project = await self._session.get(ProjectRow, project_id)
            if project is None:
                raise B2BError(f"project not found: {project_id}")
            cabinet_id = project.cabinet_id
        if not cabinet_id:
            return {"ok": False, "skipped": "b2b_center_target_missing"}

        instances = ModuleInstanceService(self._session)
        instance = await instances.resolve_sot_instance(
            module_id=MODULE_ID,
            owner_kind=OWNER_PROJECT if project_id else OWNER_CABINET,
            owner_id=project_id or cabinet_id or "",
        )
        if instance is None:
            raise B2BError("mod_equipment is not bound to the configured project/cabinet")

        stats: dict[str, Any] = {
            "ok": True,
            "created": 0,
            "updated": 0,
            "unchanged": 0,
            "filtered": 0,
            "failed": 0,
            "refs": 0,
            "cards_fetched": 0,
            "project_id": project_id,
            "cabinet_id": cabinet_id,
        }
        started = datetime.now(UTC)
        client = self._make_client()
        try:
            refs = await self._list_refs(client, stats)
            stats["refs"] = len(refs)
            existing_rows = await instances.list_data_rows(
                instance_id=instance.id, table_slug=TENDERS_TABLE
            )
            by_ref: dict[str, dict[str, Any]] = {}
            for row in existing_rows:
                body = row.get("body") if isinstance(row.get("body"), dict) else {}
                ref = str(body.get("source_ref") or "").strip()
                if ref:
                    by_ref[ref] = row
            flt = B2BCategoryFilter(
                categories=list(cfg.b2b_center_categories or []),
                min_remaining_hours=cfg.b2b_center_min_remaining_hours,
                skip_not_open=cfg.b2b_center_skip_not_open,
                keywords_include=list(cfg.b2b_center_keywords_include or []),
                keywords_exclude=list(cfg.b2b_center_keywords_exclude or []),
            )
            # Refresh due known tenders first (description/positions updates),
            # then discover new refs, then known-but-not-due. With a per-run cap
            # this prevents both starvation directions.
            def _order(ref: B2BTradeRef) -> tuple[int, str]:
                body = (by_ref.get(f"b2b:{ref.uid}") or {}).get("body")
                if isinstance(body, dict) and self._is_due(body, ref):
                    return (0, ref.uid)
                if f"b2b:{ref.uid}" not in by_ref:
                    return (1, ref.uid)
                return (2, ref.uid)

            refs.sort(key=_order)
            limit = max(1, int(cfg.b2b_center_max_cards_per_run))
            for ref in refs[:limit]:
                source_ref = f"b2b:{ref.uid}"
                row = by_ref.get(source_ref)
                old_body = row.get("body") if row and isinstance(row.get("body"), dict) else None
                if old_body and not force and not self._is_due(old_body, ref):
                    stats["unchanged"] += 1
                    continue
                try:
                    card = await client.get_card(ref.uid)
                    stats["cards_fetched"] += 1
                except B2BError as exc:
                    stats["failed"] += 1
                    stats.setdefault("errors", []).append(f"{ref.uid}: {exc}"[:300])
                    continue
                decision = flt.match(card)
                if not decision.matched:
                    stats["filtered"] += 1
                    if old_body:
                        await self._refresh_lifecycle(instances, instance.id, str(row["row_id"]), old_body)
                    continue
                new_body = card_to_tender_body(
                    card,
                    category=decision.category,
                    streams=self._stream_for(ref.uid, refs),
                    existing=old_body,
                )
                if old_body and old_body.get("content_hash") == new_body.get("content_hash"):
                    stats["unchanged"] += 1
                    continue
                await instances.upsert_data_row(
                    instance_id=instance.id,
                    table_slug=TENDERS_TABLE,
                    row_id=str(row["row_id"]) if row else stable_row_id(source_ref),
                    body=new_body,
                    created_by="b2b_import",
                )
                if old_body:
                    stats["updated"] += 1
                    stats.setdefault("changed", []).append(
                        {"row_id": str(row["row_id"]), "fields": changed_fields(old_body, new_body)[:20]}
                    )
                else:
                    stats["created"] += 1
            await self._save_state(instances, instance.id, started, stats)
            await self._session.commit()
            return stats
        except Exception as exc:
            await self._session.rollback()
            await self._save_failed_state(started, str(exc)[:1000])
            raise
        finally:
            await client.close()

    # ---------------------------------------------------------------- helpers
    def _make_client(self) -> B2BCenterClient:
        if self._client_factory is not None:
            return self._client_factory()
        cfg = self._settings
        return B2BCenterClient(
            base_url=cfg.b2b_center_base_url,
            login=cfg.b2b_center_login,
            password=cfg.b2b_center_password,
        )

    async def _list_refs(self, client: B2BCenterClient, stats: dict[str, Any]) -> list[B2BTradeRef]:
        refs: dict[str, B2BTradeRef] = {}
        for name in ("recommended_list", "trades_with_me_list"):
            try:
                items = await getattr(client, name)()
            except B2BError as exc:
                stats.setdefault("errors", []).append(f"{name}: {exc}"[:300])
                continue
            for item in items:
                prev = refs.get(item.uid)
                if prev is None or (item.changed_at and (prev.changed_at is None or item.changed_at > prev.changed_at)):
                    refs[item.uid] = item
        return list(refs.values())

    @staticmethod
    def _stream_for(uid: str, refs: list[B2BTradeRef]) -> str:
        # The list endpoints don't expose their name on the merged object; keep a
        # useful provenance marker. Detailed stream attribution is not critical
        # for the module, but `source_streams` stays populated for diagnostics.
        return "b2b-center"

    def _is_due(self, body: dict[str, Any], ref: B2BTradeRef) -> bool:
        raw = str(body.get("last_synced_at") or "").strip()
        if not raw:
            return True
        try:
            last = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            return True
        if last.tzinfo is None:
            last = last.replace(tzinfo=UTC)
        interval = max(60, int(self._settings.b2b_center_interval_sec))
        if (datetime.now(UTC) - last).total_seconds() < interval:
            return False
        return True

    async def _refresh_lifecycle(
        self,
        instances: ModuleInstanceService,
        instance_id: str,
        row_id: str,
        old_body: dict[str, Any],
    ) -> None:
        lifecycle = lifecycle_from_deadline(old_body.get("deadline_at"))
        if old_body.get("lifecycle_status") == lifecycle:
            return
        patch = dict(old_body)
        patch["lifecycle_status"] = lifecycle
        await instances.upsert_data_row(
            instance_id=instance_id,
            table_slug=TENDERS_TABLE,
            row_id=row_id,
            body=patch,
            created_by="b2b_import",
        )

    async def _save_state(
        self,
        instances: ModuleInstanceService,
        instance_id: str,
        started: datetime,
        stats: dict[str, Any],
    ) -> None:
        await instances.upsert_data_row(
            instance_id=instance_id,
            table_slug=STATE_TABLE,
            row_id=STATE_ROW_ID,
            body={
                "last_run_at": started.isoformat(),
                "finished_at": datetime.now(UTC).isoformat(),
                "stats": stats,
            },
            created_by="b2b_import",
        )

    async def _save_failed_state(self, started: datetime, error: str) -> None:
        """The write transaction was rolled back; only log (no recursive run)."""
        _log.warning("b2b import failed started=%s error=%s", started.isoformat(), error)


def make_client_from_settings(settings: Settings) -> B2BCenterClient:
    return B2BCenterClient(
        base_url=settings.b2b_center_base_url,
        login=settings.b2b_center_login,
        password=settings.b2b_center_password,
    )
