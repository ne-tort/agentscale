"""Scratch probe (not a test): is the sibling mirror's default lost on a second build?"""

from __future__ import annotations

import sys

sys.path.insert(0, ".")

import sqlalchemy as sa  # noqa: E402
from sqlalchemy.dialects import sqlite  # noqa: E402

from tests.migration.dynamic_app_gateway.sqlite_mirror import scope_metadata  # noqa: E402


def ddl(metadata: sa.MetaData) -> str:
    return str(sa.schema.CreateTable(metadata.tables["companies"]).compile(dialect=sqlite.dialect()))


first = scope_metadata(sa.MetaData())
print("first build has default:", "DEFAULT CURRENT_TIMESTAMP" in ddl(first))
second = scope_metadata(sa.MetaData())
print("second build has default:", "DEFAULT CURRENT_TIMESTAMP" in ddl(second))
print("first build after second:", "DEFAULT CURRENT_TIMESTAMP" in ddl(first))
