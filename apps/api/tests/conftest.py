"""Pytest fixtures — shared gates and DB helpers."""

import asyncio
import os
import shutil
import socket
import subprocess
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from urllib.parse import urlparse

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from prodavan.main import create_app

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql+asyncpg://prodavan_app:prodavan@localhost:5432/prodavan",
)


def _postgres_available() -> bool:
    """TCP reachability only — avoid asyncio.run() (breaks Windows Proactor + TestClient)."""
    raw = DATABASE_URL.replace("postgresql+asyncpg://", "postgresql://", 1)
    parsed = urlparse(raw)
    host = parsed.hostname or "localhost"
    port = parsed.port or 5432
    try:
        with socket.create_connection((host, port), timeout=1.0):
            return True
    except OSError:
        return False


requires_postgres = pytest.mark.skipif(
    not _postgres_available(),
    reason="PostgreSQL not available at DATABASE_URL",
)

E2E_BASE_URL = os.getenv("PRODAVAN_E2E_BASE_URL", "http://127.0.0.1:8088").rstrip("/")
K8S_SANDBOX_NAMESPACE = os.getenv("POD_SANDBOX_NAMESPACE", "prodavan-sandboxes")


def _live_e2e_base_url() -> str:
    return os.getenv("PRODAVAN_E2E_BASE_URL", "http://127.0.0.1:8088").rstrip("/")


def _live_api_available() -> bool:
    url = f"{_live_e2e_base_url()}/health/live"
    wait_sec = float(os.getenv("PRODAVAN_E2E_LIVE_WAIT_SEC", "0") or "0")
    attempts = max(1, int(wait_sec / 2) + 1) if wait_sec > 0 else 1
    delay = 2.0 if wait_sec > 0 else 0.0
    import time

    last_err: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            with urllib.request.urlopen(url, timeout=3.0) as resp:
                if resp.status == 200:
                    if attempt > 1:
                        print(f"live API ready at {url} (attempt {attempt})")
                    return True
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            last_err = exc
        if attempt < attempts:
            time.sleep(delay)
    if last_err is not None and attempts > 1:
        print(f"live API not ready at {url}: {last_err}")
    return False


def _k8s_available() -> bool:
    sa_token = os.path.join("/var/run/secrets/kubernetes.io/serviceaccount", "token")
    if os.path.isfile(sa_token):
        return True
    kubectl = shutil.which("kubectl")
    if kubectl is None:
        return False
    try:
        proc = subprocess.run(
            [
                kubectl,
                "auth",
                "can-i",
                "list",
                "pods",
                "-n",
                K8S_SANDBOX_NAMESPACE,
            ],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        return proc.returncode == 0 and proc.stdout.strip().lower() == "yes"
    except (OSError, subprocess.TimeoutExpired):
        return False


requires_live_api = pytest.mark.skipif(
    not _live_api_available(),
    reason=f"Live API not reachable at {_live_e2e_base_url()}/health/live",
)

requires_k8s = pytest.mark.skipif(
    not _k8s_available(),
    reason="Kubernetes sandboxes namespace not accessible (in-cluster SA or kubectl)",
)


def sql_backdate_project(project_id: str, updated_at: datetime) -> None:
    """Set projects.updated_at/created_at for idle-pause sweep tests."""

    async def _run() -> None:
        engine = create_async_engine(DATABASE_URL, pool_pre_ping=True)
        async with engine.begin() as conn:
            await conn.execute(
                text("UPDATE projects SET updated_at = :ts, created_at = :ts WHERE id = :pid"),
                {"ts": updated_at, "pid": project_id},
            )
        await engine.dispose()

    def _runner() -> None:
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(_run())
        finally:
            loop.close()

    with ThreadPoolExecutor(max_workers=1) as pool:
        pool.submit(_runner).result(timeout=30)


def _run_async(coro_factory, *, timeout: float) -> None:
    """Run async work on a fresh event loop in a worker thread (Windows Proactor safe)."""

    def _runner() -> None:
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(coro_factory())
        finally:
            loop.close()

    with ThreadPoolExecutor(max_workers=1) as pool:
        pool.submit(_runner).result(timeout=timeout)


def _dispose_app_engine() -> None:
    """Release pooled DB connections so TRUNCATE cannot wait on locks forever."""
    import prodavan.infrastructure.persistence.database as db

    async def _dispose() -> None:
        await db.dispose_engine()

    _run_async(_dispose, timeout=45)


def _wipe_public_tables() -> None:
    """Truncate app tables in a worker thread so Windows Proactor stays intact."""

    async def _wipe() -> None:
        engine = create_async_engine(
            DATABASE_URL,
            pool_pre_ping=True,
            connect_args={"timeout": 10},
        )
        async with engine.begin() as conn:
            await conn.execute(text("SET lock_timeout = '5s'"))
            await conn.execute(text("SET statement_timeout = '30s'"))
            result = await conn.execute(
                text(
                    "SELECT tablename FROM pg_tables "
                    "WHERE schemaname = 'public' AND tablename <> 'alembic_version'"
                )
            )
            tables = [r[0] for r in result.all()]
            if tables:
                quoted = ", ".join(f'"{t}"' for t in tables)
                await conn.execute(text(f"TRUNCATE {quoted} CASCADE"))
        await engine.dispose()

    _run_async(_wipe, timeout=60)


def _seed_integration_catalog() -> None:
    """Re-insert product modules + platform bootstrap after TRUNCATE (mirrors Alembic seed)."""
    import json

    from prodavan.application.platform.product_module_seeds import PRODUCT_MODULES

    async def _seed() -> None:
        engine = create_async_engine(DATABASE_URL, pool_pre_ping=True)
        async with engine.begin() as conn:
            for module_id, name, slugs in PRODUCT_MODULES:
                await conn.execute(
                    text(
                        """
                        INSERT INTO modules (id, name, status)
                        VALUES (:id, :name, 'active')
                        ON CONFLICT (id) DO UPDATE SET name = EXCLUDED.name, status = 'active'
                        """
                    ),
                    {"id": module_id, "name": name},
                )
                for slug, body in slugs.items():
                    doc_id = f"mmd_{module_id.removeprefix('mod_')}_{slug}"
                    await conn.execute(
                        text(
                            """
                            INSERT INTO module_meta_documents (id, module_id, slug, body)
                            VALUES (:id, :module_id, :slug, CAST(:body AS jsonb))
                            ON CONFLICT (module_id, slug) DO UPDATE SET body = EXCLUDED.body
                            """
                        ),
                        {
                            "id": doc_id,
                            "module_id": module_id,
                            "slug": slug,
                            "body": json.dumps(body),
                        },
                    )
        await engine.dispose()

    _run_async(_seed, timeout=90)
    _dispose_app_engine()


def pytest_collection_modifyitems(config, items) -> None:
    """Apply layer markers from test path (conftest pytestmark is not inherited)."""
    for item in items:
        path = str(getattr(item, "fspath", "")).replace("\\", "/")
        if "/tests/integration/" in path:
            item.add_marker(pytest.mark.integration)
        elif "/tests/e2e/k8s/" in path:
            item.add_marker(pytest.mark.k8s)
        elif "/tests/e2e/live/" in path:
            item.add_marker(pytest.mark.live)


def _test_needs_postgres_wipe(request: pytest.FixtureRequest) -> bool:
    """TRUNCATE+seed only for tests that declare @requires_postgres (or k8s e2e)."""
    path = str(getattr(request, "fspath", "")).replace("\\", "/")
    if "/tests/e2e/k8s/" in path:
        return True
    if "/tests/integration/test_health.py" in path:
        return False
    if "/tests/integration/" not in path:
        return False
    for mark in request.node.iter_markers("skipif"):
        if "PostgreSQL" in str(mark.kwargs.get("reason", "")):
            return True
    return False


@pytest.fixture(autouse=True)
def clean_engine_cache(request: pytest.FixtureRequest):
    """Dispose pooled connections; wipe DB only when the test may use it."""
    _dispose_app_engine()
    path = str(getattr(request, "fspath", "")).replace("\\", "/")
    uses_client = "async_client" in request.fixturenames
    # Pure unit tests without AsyncClient do not touch Postgres — skip TRUNCATE
    # (CI shared PG under Docker Desktop load otherwise flakes with TimeoutError).
    needs_wipe = _postgres_available() and (
        _test_needs_postgres_wipe(request)
        or (uses_client and "/tests/unit/" not in path and "/tests/integration/" not in path)
    )
    if needs_wipe:
        last_err: TimeoutError | None = None
        for _ in (1, 2, 3, 4):
            try:
                _wipe_public_tables()
                last_err = None
                break
            except TimeoutError as exc:
                last_err = exc
                _dispose_app_engine()
        if last_err is not None:
            raise last_err
        if "/tests/integration/" in path or "/tests/e2e/k8s/" in path:
            seed_err: TimeoutError | None = None
            for _ in (1, 2, 3):
                try:
                    _seed_integration_catalog()
                    seed_err = None
                    break
                except TimeoutError as exc:
                    seed_err = exc
                    _dispose_app_engine()
            if seed_err is not None:
                raise seed_err
    yield
    _dispose_app_engine()


@pytest_asyncio.fixture
async def async_client():
    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
