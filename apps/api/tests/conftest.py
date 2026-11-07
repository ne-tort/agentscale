"""Pytest fixtures — shared gates and DB helpers.

Reachability of the optional services is memoised. The PostgreSQL probe is a TCP
connect to a port that is normally closed, and on this platform an IPv4 loopback
connect to a closed port is *dropped* rather than refused, so a single probe costs
the whole ``timeout``. Calling it per test (the autouse ``clean_engine_cache``
fixture did) turned a 30-second unit run into tens of minutes of near-idle socket
waiting — indistinguishable from a hang.
"""

import asyncio
import os
import shutil
import socket
import subprocess
import time
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

from prodavan.config.settings import settings
from prodavan.main import create_app

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql+asyncpg://prodavan_app:prodavan@localhost:5432/prodavan",
)

_POSTGRES_AVAILABLE: bool | None = None
_POSTGRES_PROBED_AT: float = 0.0

NEGATIVE_PROBE_TTL_SEC = 300.0
"""How long "no PostgreSQL" is trusted before the socket is asked again.

A *positive* answer is kept for the whole process (the endpoint does not grow a
database mid-run), but a negative one expires, so a long session that starts
PostgreSQL between runs re-detects it instead of staying blind for ever.
"""


def _probe_postgres(timeout: float = 1.0) -> bool:
    """TCP reachability of ``DATABASE_URL`` — one connect, no caching."""
    raw = DATABASE_URL.replace("postgresql+asyncpg://", "postgresql://", 1)
    parsed = urlparse(raw)
    host = parsed.hostname or "localhost"
    port = parsed.port or 5432
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _postgres_available() -> bool:
    """``True`` when PostgreSQL answers at ``DATABASE_URL`` (memoised, see above)."""
    global _POSTGRES_AVAILABLE, _POSTGRES_PROBED_AT
    now = time.monotonic()
    if _POSTGRES_AVAILABLE is True:
        return True
    if _POSTGRES_AVAILABLE is False and now - _POSTGRES_PROBED_AT < NEGATIVE_PROBE_TTL_SEC:
        return False
    _POSTGRES_AVAILABLE = _probe_postgres()
    _POSTGRES_PROBED_AT = now
    return _POSTGRES_AVAILABLE


def _reset_postgres_available() -> None:
    """Forget the memo (a test that opens/closes a database itself may need this)."""
    global _POSTGRES_AVAILABLE, _POSTGRES_PROBED_AT
    _POSTGRES_AVAILABLE = None
    _POSTGRES_PROBED_AT = 0.0


requires_postgres = pytest.mark.skipif(
    not _postgres_available(),
    reason="PostgreSQL not available at DATABASE_URL",
)

E2E_BASE_URL = os.getenv("PRODAVAN_E2E_BASE_URL", "http://127.0.0.1:8088").rstrip("/")
K8S_SANDBOX_NAMESPACE = os.getenv("POD_SANDBOX_NAMESPACE", "agentscale-dev-sandboxes")


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


_PUBLIC_USER_TRIGGER_STATES_SQL = """
SELECT c.relname AS table_name,
       t.tgname AS trigger_name,
       t.tgenabled AS enabled
FROM pg_trigger t
JOIN pg_class c ON c.oid = t.tgrelid
JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE NOT t.tgisinternal
  AND n.nspname = 'public'
ORDER BY 1, 2
"""
"""Every app-owned (non-internal) trigger in ``public`` with its enable state.

``tgenabled`` is ``O`` for a normally active trigger, ``D`` for a disabled one.
The fixture only ever moves between those two values.
"""

_PUBLIC_TABLES_WITH_USER_TRIGGERS_SQL = """
SELECT DISTINCT c.relname
FROM pg_trigger t
JOIN pg_class c ON c.oid = t.tgrelid
JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE NOT t.tgisinternal
  AND n.nspname = 'public'
  AND c.relkind IN ('r', 'p')
ORDER BY 1
"""

_PUBLIC_DISABLED_TRIGGER_TABLES_SQL = """
SELECT DISTINCT c.relname
FROM pg_trigger t
JOIN pg_class c ON c.oid = t.tgrelid
JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE NOT t.tgisinternal
  AND n.nspname = 'public'
  AND t.tgenabled = 'D'
ORDER BY 1
"""


def _quote_ident(name: str) -> str:
    """Quote a catalog-supplied identifier for an ``ALTER TABLE``."""
    return '"' + name.replace('"', '""') + '"'


#: ``ALTER TABLE ... DISABLE TRIGGER`` requires table **ownership**, not TRUNCATE
#: privilege. Whether this session owns what it truncates is a property of the
#: deployment, not of the suite: a superuser owns everything, while a dedicated
#: app role that was granted DML by the migration step owns nothing. Ask the
#: catalog instead of assuming, and let the TRUNCATE error speak when the guard
#: is up and the role cannot step around it -- that is a real failure, not a
#: privilege gap.
_PUBLIC_OWNED_TABLES_SQL = """
SELECT c.relname
FROM pg_class c
JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname = 'public'
  AND c.relkind IN ('r', 'p')
  AND pg_catalog.pg_has_role(current_user, c.relowner, 'USAGE')
ORDER BY 1
"""


async def _ensure_user_triggers_enabled(engine) -> None:
    """Re-enable USER triggers on every ``public`` table that has one disabled.

    Catalog-driven and self-contained (own transaction, own round-trip), so it
    still works when the caller's transaction is aborted. It runs before the wipe
    (a previous crashed run must not make this run falsely green) and after it
    (this run must not hand a guard-less database to the next test).

    Only owned tables are touched: ``ENABLE TRIGGER`` needs ownership just like
    ``DISABLE`` does, and asking for a table this session cannot alter would turn
    the repair pass itself into the failure.
    """
    async with engine.begin() as conn:
        await conn.execute(text("SET lock_timeout = '5s'"))
        owned = {
            row[0] for row in (await conn.execute(text(_PUBLIC_OWNED_TABLES_SQL))).all()
        }
        result = await conn.execute(text(_PUBLIC_DISABLED_TRIGGER_TABLES_SQL))
        for (table_name,) in result.all():
            if table_name not in owned:
                continue
            await conn.execute(
                text(f"ALTER TABLE public.{_quote_ident(table_name)} ENABLE TRIGGER USER")
            )


def _trigger_state(value) -> str:
    """Normalise ``pg_trigger.tgenabled``.

    The column is PostgreSQL's one-byte ``"char"`` type; asyncpg hands it back as
    ``bytes`` (``b'O'``), while other drivers give ``str``. Compare as text.
    """
    if isinstance(value, (bytes, bytearray)):
        return value.decode("ascii")
    return str(value)


def public_user_trigger_states() -> list[tuple[str, str, str]]:
    """``(table, trigger, tgenabled)`` for every app-owned trigger in ``public``."""

    async def _read() -> list[tuple[str, str, str]]:
        engine = create_async_engine(DATABASE_URL, pool_pre_ping=True)
        try:
            async with engine.connect() as conn:
                result = await conn.execute(text(_PUBLIC_USER_TRIGGER_STATES_SQL))
                return [(r[0], r[1], _trigger_state(r[2])) for r in result.all()]
        finally:
            await engine.dispose()

    box: dict[str, list[tuple[str, str, str]]] = {}

    def _run() -> None:
        box["rows"] = asyncio.run(_read())

    with ThreadPoolExecutor(max_workers=1) as pool:
        pool.submit(_run).result(timeout=60)
    return box["rows"]


def _wipe_public_tables() -> None:
    """Truncate app tables in a worker thread so Windows Proactor stays intact.

    Why the trigger dance: several migrations install *append-only* guards — on
    ``runtime_materialization_audit`` (``2026110306_resources_runtime``), on the
    M04E ledger tables (``2026110315_m04e_ledger``), on the M06 cutover evidence
    table (``2026110316_m06_cutover_evidence``) and on
    ``agent_control_plane_events`` — whose ``BEFORE UPDATE OR DELETE`` and
    ``BEFORE TRUNCATE`` triggers raise on purpose. Production must keep them, so a
    bare ``TRUNCATE`` fails closed with ``... is append-only``.

    The fixture is what is wrong here, not the invariant: it resets the whole
    world between tests and therefore has to step *through* the guard. The
    contract is: drop USER triggers for the duration of the wipe and switch them
    back on before control returns to the test. Leaving them off would make the
    next run *falsely* green — every append-only test would pass against a
    database that enforces nothing.

    The guarded tables are read from ``pg_trigger``/``pg_class``, never
    hard-coded, so a future append-only table cannot break the fixture again.
    ``DISABLE``, ``TRUNCATE`` and ``ENABLE`` share one transaction (a rollback
    restores the flags by itself); the repair pass in ``finally`` covers what a
    transaction cannot — a killed worker, or a committed-but-crashed run left
    behind by an earlier process.

    Ownership decides whether the dance is possible at all: ``ALTER TABLE``
    needs it, ``TRUNCATE`` does not. When the session owns the tables it steps
    through the guards; when it does not, the guards stay up and a ``BEFORE
    TRUNCATE`` one fails the wipe loudly instead of being silently skipped.
    """

    async def _wipe() -> None:
        engine = create_async_engine(
            DATABASE_URL,
            pool_pre_ping=True,
            connect_args={"timeout": 10},
        )
        try:
            # Entry repair, before the truncate is allowed to trust the database.
            await _ensure_user_triggers_enabled(engine)
            async with engine.begin() as conn:
                await conn.execute(text("SET lock_timeout = '5s'"))
                await conn.execute(text("SET statement_timeout = '30s'"))
                # Only tables this session can actually ALTER: DISABLE TRIGGER
                # needs ownership, and a role with DML-only grants (the CI app
                # role) would otherwise abort the wipe with "must be owner of
                # table ...". A guard on a table this role cannot alter is left
                # in place -- then TRUNCATE fails closed, which is the honest
                # outcome rather than a silently downgraded invariant.
                owned = {
                    row[0]
                    for row in (
                        await conn.execute(text(_PUBLIC_OWNED_TABLES_SQL))
                    ).all()
                }
                guarded = [
                    row[0]
                    for row in (
                        await conn.execute(text(_PUBLIC_TABLES_WITH_USER_TRIGGERS_SQL))
                    ).all()
                    if row[0] in owned
                ]
                result = await conn.execute(
                    text(
                        "SELECT tablename FROM pg_tables "
                        "WHERE schemaname = 'public' AND tablename <> 'alembic_version'"
                    )
                )
                tables = [r[0] for r in result.all()]

                # FK constraint triggers are *not* USER triggers, so referential
                # integrity keeps being enforced across the truncate.
                for table_name in guarded:
                    await conn.execute(
                        text(
                            f"ALTER TABLE public.{_quote_ident(table_name)} "
                            "DISABLE TRIGGER USER"
                        )
                    )
                try:
                    if tables:
                        quoted = ", ".join(f'"{t}"' for t in tables)
                        await conn.execute(text(f"TRUNCATE {quoted} CASCADE"))
                finally:
                    # Same transaction on purpose: it runs on the happy path *and*
                    # when the truncate raised, and if these statements never get
                    # to run the whole block rolls back and restores the flags.
                    for table_name in guarded:
                        await conn.execute(
                            text(
                                f"ALTER TABLE public.{_quote_ident(table_name)} "
                                "ENABLE TRIGGER USER"
                            )
                        )
        finally:
            # Last line of defence, on a fresh connection: runs even when the block
            # above blew up, and raises rather than leave the guards off.
            await _ensure_user_triggers_enabled(engine)
            await engine.dispose()

    _run_async(_wipe, timeout=60)


def _seed_integration_catalog() -> None:
    """Re-insert product modules + platform bootstrap after TRUNCATE (mirrors Alembic seed)."""
    import json

    from prodavan.application.platform.product_module_seeds import (
        LEGACY_MODULE_ROWS,
        PRODUCT_MODULES,
        PRODUCT_MODULE_COMPANY_GRANT_SCOPE,
    )

    legacy_ids = {module_id for module_id, _ in LEGACY_MODULE_ROWS}

    async def _seed() -> None:
        engine = create_async_engine(DATABASE_URL, pool_pre_ping=True)
        async with engine.begin() as conn:
            for module_id, name, slugs in PRODUCT_MODULES:
                await conn.execute(
                    text(
                        """
                        INSERT INTO modules (id, name, status, company_grant_scope)
                        VALUES (:id, :name, :status, :company_grant_scope)
                        ON CONFLICT (id) DO UPDATE
                        SET name = EXCLUDED.name, status = EXCLUDED.status
                        """
                    ),
                    {
                        "id": module_id,
                        "name": name,
                        # Mirrors upsert_product_modules: the modules folded into
                        # «Управление» are archived shells, «Управление» itself is
                        # granted to every company.
                        "status": "archived" if module_id in legacy_ids else "active",
                        "company_grant_scope": PRODUCT_MODULE_COMPANY_GRANT_SCOPE.get(
                            module_id, "selected"
                        ),
                    },
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


def _seed_platform_bootstrap() -> None:
    """Ensure cab_basic exists after TRUNCATE (mirrors API lifespan bootstrap)."""

    async def _seed() -> None:
        from prodavan.application.platform.bootstrap_service import PlatformBootstrapService
        from prodavan.infrastructure.persistence.database import get_session_factory

        factory = get_session_factory()
        async with factory() as session:
            await PlatformBootstrapService(session).ensure_bootstrapped()

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


def _reset_auth_test_state() -> None:
    """Reset process-global fake auth + kafka buffer (integration / k8s e2e isolation)."""
    from prodavan.infrastructure.auth.jwt import reset_jwt_validator
    from prodavan.infrastructure.keycloak.invite import reset_invite_client

    reset_jwt_validator()
    reset_invite_client()
    try:
        from prodavan.core.infra.kafka_manager import get_kafka_manager_optional

        mgr = get_kafka_manager_optional()
        if mgr is not None:
            mgr.reset_local_test_state()
    except Exception:
        pass


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
def _integration_stub_runtime(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> None:
    """Integration tests use stub pod runtime (no k8s cluster required)."""
    path = str(getattr(request, "fspath", "")).replace("\\", "/")
    if "/tests/integration/" not in path:
        return
    monkeypatch.setenv("POD_RUNTIME_MODE", "stub")
    monkeypatch.setattr(settings, "pod_runtime_mode", "stub")
    monkeypatch.setattr(settings, "pod_agent_runtime_enabled", False)
    monkeypatch.setattr(settings, "agent_inprocess_adapters_enabled", True)
    # Pod launch mints a scoped bridge JWT, and ``mint_pod_bridge_token`` is
    # fail-closed on a missing secret (``pod_identity_bridge_strict``). That
    # invariant belongs to production and is covered by
    # ``tests/unit/test_tenant_infra_bridge.py``; the integration layer has no
    # deployment to take the secret from, so it configures one exactly as a
    # deployment would. Setting it here (rather than per test) keeps every
    # launch/pod-lifecycle test from failing on configuration instead of code.
    monkeypatch.setenv("POD_IDENTITY_BRIDGE_SECRET", "integration-bridge-secret")
    monkeypatch.setattr(settings, "pod_identity_bridge_secret", "integration-bridge-secret")
    # Bridge-token *revocation* keeps its generation counter in Redis, and strict
    # mode turns an unavailable cache into 503 POD_BRIDGE_REVOCATION_FAILED so a
    # pause cannot leave a revoked token valid. That is the production contract
    # and it stays covered by tests/unit. The integration layer runs against a bare
    # PostgreSQL (no Redis, no k8s — same shape as the nightly CI job), so it uses
    # the documented dev/test path: non-strict mode mirrors the generation into the
    # in-process ``_GEN_FALLBACK``, which is enough for a single-process suite.
    monkeypatch.setattr(settings, "pod_identity_bridge_strict", False)


@pytest.fixture(autouse=True)
def clean_engine_cache(request: pytest.FixtureRequest):
    """Dispose pooled connections; wipe DB only when the test may use it."""
    _dispose_app_engine()
    path = str(getattr(request, "fspath", "")).replace("\\", "/")
    uses_client = "async_client" in request.fixturenames
    postgres_wipe = _test_needs_postgres_wipe(request)
    # Pure unit tests without AsyncClient do not touch Postgres — skip TRUNCATE
    # (CI shared PG under Docker Desktop load otherwise flakes with TimeoutError).
    # Integration/k8s: always wipe when the test declares @requires_postgres — do not
    # gate on runtime TCP probe (1s timeout flakes under Docker Desktop load and leaves
    # stale keycloak_sub rows while FakeUserAdmin resets subs to kc_fake_1…).
    # The cheap path predicates come *before* the database probe on purpose: the probe
    # can cost a full socket timeout, and the overwhelming majority of tests never
    # touch a client at all.
    needs_wipe = postgres_wipe or (
        uses_client
        and "/tests/unit/" not in path
        and "/tests/integration/" not in path
        and _postgres_available()
    )
    if needs_wipe:
        _reset_auth_test_state()
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
            bootstrap_err: TimeoutError | None = None
            for _ in (1, 2, 3):
                try:
                    _seed_platform_bootstrap()
                    bootstrap_err = None
                    break
                except TimeoutError as exc:
                    bootstrap_err = exc
                    _dispose_app_engine()
            if bootstrap_err is not None:
                raise bootstrap_err
    yield
    _dispose_app_engine()
    if needs_wipe:
        _reset_auth_test_state()


@pytest_asyncio.fixture
async def async_client():
    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
