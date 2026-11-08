"""Общие данные для unit-тестов F0.

Файлы schema.json / protocol.json / types.ts читаются как данные (без импортов).
"""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

from prodavan.domain.plugins.validation import PROTOCOL_RELATIVE_PATH, SCHEMA_RELATIVE_PATH


def repo_root() -> Path:
    """Корень репозитория по расположению этого файла: tests/unit/plugins/domain/."""
    return Path(__file__).resolve().parents[5]


def schema_path() -> Path:
    """Путь к JSON Schema манифеста."""
    return (repo_root() / SCHEMA_RELATIVE_PATH).resolve()


def protocol_path() -> Path:
    """Путь к контракту plugin-rpc/1."""
    return (repo_root() / PROTOCOL_RELATIVE_PATH).resolve()


def types_ts_path() -> Path:
    """Путь к TypeScript-типам."""
    return (repo_root() / "packages/schemas/plugin-manifest/v1/types.ts").resolve()


VALID_DIGEST = "sha256:" + "a" * 64
"""Валидный artifact_digest (не входит в манифест!)."""


def valid_manifest_dict() -> dict[str, Any]:
    """Валидный манифест (эталон из §5.1 v4, дополненный обязательными полями)."""
    return {
        "plugin_id": "mod_equipment",
        "version": "1.2.0",
        "protocol": "plugin-rpc/1",
        "frontend": {"entry": "index.html", "base_path": "/pluginpage/mod_equipment/"},
        "backend": {"port": 8080, "health": "/health"},
        "capabilities": [
            "cache", "objects", "docs", "events", "jobs", "pods", "mcp", "secrets", "network",
        ],
        "migrations": {"engine": "liquibase", "path": "migrations/liquibase/changelog.xml"},
        "egress_ref": "grant:co_123/mod_equipment/inst_abc",
        "public_pages": [],
        "webhooks": ["/webhook/external-system"],
        "min_platform_version": "1.0.0",
    }


def with_patch(**changes: Any) -> dict[str, Any]:
    """Копия валидного манифеста с точечной заменой полей верхнего уровня."""
    payload = copy.deepcopy(valid_manifest_dict())
    payload.update(changes)
    return payload


def without_field(field_name: str) -> dict[str, Any]:
    """Копия валидного манифеста без одного поля."""
    payload = copy.deepcopy(valid_manifest_dict())
    payload.pop(field_name)
    return payload
