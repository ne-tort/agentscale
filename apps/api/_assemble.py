"""Assemble ``m04e_ports.py`` from its unchanged head and the new store tail."""

import pathlib

target = pathlib.Path("src/prodavan/application/migration/m04e_ports.py")
source = target.read_text(encoding="utf-8")
marker = "# ---------------------------------------------------------------------------\n# EvidenceStore boundary"
head = source[: source.index(marker)]
tail = pathlib.Path("_tail_new.py").read_text(encoding="utf-8")

start_marker = "``EvidenceStore`` — :class:`SqlM04EEvidenceStore` **refuses**"
end_marker = "``scope_owner_audit``).\n"
start = head.index(start_marker)
end = head.index(end_marker) + len(end_marker)
new_doc = pathlib.Path("_doc_new.md").read_text(encoding="utf-8")
head = head[:start] + new_doc + head[end:]

old_imports = """import hashlib
import json
from collections.abc import AsyncIterator, Mapping, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Final

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from prodavan.application.migration.m04e_definition_version.backfill import (
"""
new_imports = """import hashlib
import json
import re
import uuid
from collections.abc import AsyncIterator, Mapping, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass, replace
from enum import StrEnum
from typing import Any, Final

import sqlalchemy as sa
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from prodavan.application.migration.m04e_definition_version.backfill import (
    DefinitionVersionEvidenceBackfill,
"""
assert old_imports in head
head = head.replace(old_imports, new_imports)

old_tail_import = "    UpstreamGateEvidence,\n    UpstreamGatePort,\n)\n"
new_tail_import = """    UpstreamGateEvidence,
    UpstreamGatePort,
)
from prodavan.infrastructure.persistence.models.m04e_ledger import (
    M04EDefinitionVersionAuditRow as EVIDENCE_AUDIT_TABLE_MODEL,
    M04EDefinitionVersionCheckpointRow as EVIDENCE_CHECKPOINT_TABLE_MODEL,
    M04EDefinitionVersionMappingRow as EVIDENCE_MAPPING_TABLE_MODEL,
    M04EDefinitionVersionQuarantineRow as EVIDENCE_QUARANTINE_TABLE_MODEL,
)
"""
assert head.count(old_tail_import) == 1, head.count(old_tail_import)
head = head.replace(old_tail_import, new_tail_import)

target.write_text(head + tail, encoding="utf-8")
print("written", len(head + tail))
