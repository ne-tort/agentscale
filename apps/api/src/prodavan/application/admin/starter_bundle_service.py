"""Starter bundle catalog read model (L04)."""

from __future__ import annotations

import base64
from pathlib import Path

from prodavan.config.settings import settings
from prodavan.domain.admin.starter_catalog import STARTER_BUNDLE_CATALOG, StarterBundleEntry
from prodavan.domain.errors import AppError


class StarterBundleCatalogService:
    def list_entries(self) -> list[dict]:
        root = settings.starter_bundles_dir
        return [
            {
                "id": entry.id,
                "name": entry.name,
                "description": entry.description,
                "official": entry.official,
                "bundle_available": self._bundle_path(entry.id, root).is_file(),
            }
            for entry in STARTER_BUNDLE_CATALOG
        ]

    def get_entry(self, bundle_id: str) -> StarterBundleEntry:
        for entry in STARTER_BUNDLE_CATALOG:
            if entry.id == bundle_id:
                return entry
        raise AppError(code="NOT_FOUND", title="Not Found", status=404, detail="Starter bundle not found")

    def export_base64(self, bundle_id: str) -> dict:
        entry = self.get_entry(bundle_id)
        path = self._bundle_path(entry.id, settings.starter_bundles_dir)
        if not path.is_file():
            raise AppError(
                code="BUNDLE_NOT_SHIPPED",
                title="Bundle not available",
                status=503,
                detail=f"starter bundle file missing for {bundle_id}",
            )
        raw = path.read_bytes()
        return {
            "id": entry.id,
            "name": entry.name,
            "format": "cabinet.bundle",
            "size_bytes": len(raw),
            "zip_base64": base64.b64encode(raw).decode("ascii"),
        }

    @staticmethod
    def _bundle_path(bundle_id: str, root: Path) -> Path:
        safe = bundle_id.replace("/", "").replace("\\", "")
        return root / f"{safe}.bundle.zip"
