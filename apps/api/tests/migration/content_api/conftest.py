"""Per-test isolation for the Content API acceptance suite.

Every test in this package provisions tenants *through the real company API*
with fixed names (``ContentA`` / ``ContentB``), and company names are unique.
Without a reset between tests the second test in the file gets ``409 company
name already exists`` while building its fixtures, so the whole package
collapses into setup errors no matter how correct the code under test is.

``tests/conftest.py`` only wipes for tests it recognises as integration/k8s
(path predicate + the autouse fixtures keyed on ``async_client``); these tests
drive their own ``TestClient``, so they were never covered. The reset below is
the same one the integration layer uses — truncate everything except
``alembic_version`` (through the append-only guards, which are switched back on
afterwards) and re-seed the product module catalog and platform bootstrap that
``create_app()`` expects to find.
"""

from __future__ import annotations

import pytest

from tests.conftest import (
    _dispose_app_engine,
    _postgres_available,
    _seed_integration_catalog,
    _seed_platform_bootstrap,
    _wipe_public_tables,
)


@pytest.fixture(autouse=True)
def _fresh_tenant_world() -> None:
    """Start every test in this package from an empty, seeded database."""
    if not _postgres_available():
        # The fixtures themselves skip when PostgreSQL is absent; nothing to reset.
        # This must still be a generator: a fixture that declares ``-> None`` and
        # returns before its first ``yield`` is reported by pytest as
        # "_fresh_tenant_world did not yield a value", which turns every test in the
        # package into a setup error instead of a skip.
        yield
        return
    _dispose_app_engine()
    _wipe_public_tables()
    _seed_integration_catalog()
    _seed_platform_bootstrap()
    yield
    _dispose_app_engine()
