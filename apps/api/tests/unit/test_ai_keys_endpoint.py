"""AI keys created from an admin-entered endpoint (no provider dropdown).

The admin types `base_url` + `secret`; the endpoint is matched to (or created
as) an `ai.http_providers` catalog entry and the key links to it through
`catalog_entry_id`. These tests pin:

* the create contract and the entry payload it produces,
* entry *reuse* — two keys on one endpoint share one entry,
* validation (non-http(s) endpoints rejected with the AppError 422 shape),
* the list/get response showing where a key points,
* the legacy provider/api_kind create staying untouched,
* the probe resolving the key to that entry and hitting its base_url.

Runs against in-memory SQLite (no PostgreSQL needed); JSONB is compiled to
JSON for the sqlite dialect, same trick as `test_external_api.py`.
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.ext.compiler import compiles

from prodavan.api.v1.ai_keys import CreateKeyBody
from prodavan.application.ai_keys.endpoint import (
    base_url_key,
    display_host,
    ensure_endpoint_entry,
    find_endpoint_entry,
    slug_from_host,
)
from prodavan.application.ai_keys.service import AiKeysService
from prodavan.config.settings import settings
from prodavan.domain.errors import AppError
from prodavan.infrastructure.persistence.models.ai_keys import (
    AiKeyCheckResultRow,
    AiProviderKeyRow,
    CompanyAiKeyBindingRow,
)
from prodavan.infrastructure.persistence.models.catalog import ReferenceCatalogEntryRow
from prodavan.infrastructure.persistence.models.identity import CompanyRow


@compiles(JSONB, "sqlite")
def _jsonb_as_json(element, compiler, **kw):  # noqa: ANN001, ANN202
    return "JSON"


_TABLES = (
    CompanyRow,
    AiProviderKeyRow,
    CompanyAiKeyBindingRow,
    AiKeyCheckResultRow,
    ReferenceCatalogEntryRow,
)

CATALOG = "ai.http_providers"


class _MemorySecrets:
    """SecretStore stand-in: keeps secrets in a dict, same ref contract."""

    def __init__(self) -> None:
        self._data: dict[str, str] = {}

    def put(self, key_id: str, secret: str) -> str:
        ref = f"mem://ai_keys/{key_id}"
        self._data[ref] = secret.strip()
        return ref

    def get(self, secret_ref: str) -> str:
        return self._data[secret_ref]

    def delete(self, secret_ref: str) -> None:
        self._data.pop(secret_ref, None)

    @staticmethod
    def mask(secret: str | None) -> str | None:
        return None if not secret else f"****{secret[-4:]}"


async def _session_and_service() -> tuple[AsyncSession, AiKeysService, _MemorySecrets]:
    engine = create_async_engine("sqlite+aiosqlite://")

    async with engine.begin() as conn:
        await conn.run_sync(
            lambda sync: [t.__table__.create(sync, checkfirst=True) for t in _TABLES]
        )
    session = AsyncSession(engine, expire_on_commit=False)
    secrets = _MemorySecrets()
    return session, AiKeysService(session, secrets=secrets), secrets


def _run(coro_fn) -> Any:  # noqa: ANN001
    return asyncio.run(coro_fn())


async def _entries(session: AsyncSession) -> list[ReferenceCatalogEntryRow]:
    q = await session.execute(
        select(ReferenceCatalogEntryRow).where(ReferenceCatalogEntryRow.catalog_id == CATALOG)
    )
    return list(q.scalars().all())


async def _entry(session: AsyncSession, entry_id: str | None) -> ReferenceCatalogEntryRow | None:
    if not entry_id:
        return None
    q = await session.execute(
        select(ReferenceCatalogEntryRow).where(
            ReferenceCatalogEntryRow.catalog_id == CATALOG,
            ReferenceCatalogEntryRow.id == entry_id,
        )
    )
    return q.scalar_one_or_none()


# ---------------------------------------------------------------------------
# create with an endpoint
# ---------------------------------------------------------------------------


def test_create_with_endpoint_makes_catalog_entry() -> None:
    async def scenario() -> None:
        session, svc, _ = await _session_and_service()
        try:
            created = await svc.create_key(
                name="CheapAI key",
                secret="sk-cheapai-1",
                base_url="https://api.example.com/v1",
            )
            assert created["catalog_entry_id"], created
            assert created["api_kind"] == "custom"
            assert created["provider"] == "codex"
            # The response already shows where the key points.
            assert created["base_url"] == "https://api.example.com/v1"
            assert created["models_path"] == "/models"
            assert created["auth_scheme"] == "bearer"
            assert created["has_secret"] is True
            assert created["secret_ref_prefix"].startswith("mem://")

            entry = await _entry(session, created["catalog_entry_id"])
            assert entry is not None
            assert entry.title == "api.example.com"
            assert entry.seeded is False
            payload = entry.payload
            assert payload == {
                "api_kind": "custom",
                "agent_provider": "api-example-com",
                "base_url": "https://api.example.com/v1",
                "models_path": "/models",
                "chat_completions_path": "/chat/completions",
                "auth_scheme": "bearer",
                "openai_compatible": True,
                "supports_models_list": True,
            }
        finally:
            await session.close()

    _run(scenario)


def test_create_with_endpoint_keeps_custom_paths_and_auth() -> None:
    async def scenario() -> None:
        session, svc, _ = await _session_and_service()
        try:
            created = await svc.create_key(
                name="Anthropic-ish",
                secret="sk-ant-1",
                base_url="https://gw.example.org/",
                models_path="v1/models",
                chat_completions_path="v1/messages",
                auth_scheme="x-api-key",
            )
            assert created["base_url"] == "https://gw.example.org"
            assert created["models_path"] == "/v1/models"
            assert created["auth_scheme"] == "x-api-key"
            entry = await _entry(session, created["catalog_entry_id"])
            assert entry is not None
            assert entry.payload["chat_completions_path"] == "/v1/messages"
        finally:
            await session.close()

    _run(scenario)


def test_reuses_seeded_entry_and_inherits_its_runtime_provider() -> None:
    """`https://api.anthropic.com` already exists in the seeded catalog: the key
    links to that entry (one entry per endpoint) and keeps the provider the
    runtime expects, instead of being duplicated as a `custom` endpoint."""

    async def scenario() -> None:
        session, svc, _ = await _session_and_service()
        try:
            created = await svc.create_key(
                name="Anthropic key",
                secret="sk-ant-2",
                base_url="https://api.anthropic.com",
            )
            assert created["catalog_entry_id"] == "anthropic"
            assert created["api_kind"] == "anthropic_api"
            assert created["provider"] == "claude_code"
            assert created["base_url"] == "https://api.anthropic.com"
            assert len(await _entries(session)) == len(_SEEDED_IDS)
        finally:
            await session.close()

    _run(scenario)


_SEEDED_IDS = {"openai", "anthropic", "openrouter", "xai", "cursor", "cursor_workos", "ollama"}


def test_two_keys_on_one_endpoint_share_a_single_entry() -> None:
    async def scenario() -> None:
        session, svc, _ = await _session_and_service()
        try:
            first = await svc.create_key(
                name="A",
                secret="sk-a",
                base_url="https://API.Example.com/v1",
            )
            # Same endpoint typed differently: host case + trailing slash.
            second = await svc.create_key(
                name="B",
                secret="sk-b",
                base_url="https://api.example.com/v1/",
            )
            assert first["catalog_entry_id"] == second["catalog_entry_id"]

            target = base_url_key("https://api.example.com/v1")
            rows = [
                e for e in await _entries(session) if base_url_key(str(e.payload.get("base_url"))) == target
            ]
            assert len(rows) == 1, [e.id for e in rows]
            assert rows[0].seeded is False
            # The entry keeps the first-seen spelling; comparison is normalised.
            assert rows[0].payload["base_url"] == "https://API.Example.com/v1"
        finally:
            await session.close()

    _run(scenario)


def test_ensure_endpoint_entry_is_idempotent() -> None:
    async def scenario() -> None:
        session, _, _ = await _session_and_service()
        try:
            first = await ensure_endpoint_entry(session, base_url="http://10.0.0.5:8000/v1")
            again = await ensure_endpoint_entry(session, base_url="http://10.0.0.5:8000/v1/")
            assert first.id == again.id
            assert (await _entries(session)).count(first) == 1
            hits = [e for e in await _entries(session) if e.id == first.id]
            assert len(hits) == 1
            assert first.title == "10.0.0.5:8000"
        finally:
            await session.close()

    _run(scenario)


def test_endpoint_with_path_and_same_host_gets_distinct_ids() -> None:
    async def scenario() -> None:
        session, _, _ = await _session_and_service()
        try:
            v1 = await ensure_endpoint_entry(session, base_url="https://gw.example.com/v1")
            v2 = await ensure_endpoint_entry(session, base_url="https://gw.example.com/v2")
            assert v1.id != v2.id
            assert v1.id == "gw-example-com"
            assert v2.id.startswith("gw-example-com-")
            assert (await find_endpoint_entry(session, "https://gw.example.com/v2")) is v2
        finally:
            await session.close()

    _run(scenario)


# ---------------------------------------------------------------------------
# validation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "bad_url",
    [
        "ftp://api.example.com",
        "api.example.com/v1",  # no scheme — would be silently upgraded to https
        "",
        "   ",
        "https://",
        "https://api.example.com/v1?token=1",
        "https://user:pass@api.example.com",
        "https://api.example.com/v1#frag",
        "not a url",
        "file:///etc/passwd",
    ],
)
def test_create_rejects_non_http_url(bad_url: str) -> None:
    async def scenario() -> None:
        session, svc, _ = await _session_and_service()
        try:
            with pytest.raises(AppError) as ei:
                await svc.create_key(name="bad", secret="sk", base_url=bad_url)
            assert ei.value.code == "VALIDATION_ERROR"
            assert ei.value.status == 422
            # Nothing was created: neither a key nor a catalog entry.
            assert await _entries(session) == []
        finally:
            await session.close()

    _run(scenario)


def test_request_body_rejects_bad_endpoint_with_app_error_shape() -> None:
    """The FastAPI layer turns this into a 422 problem+json, not a 500."""
    with pytest.raises(AppError) as ei:
        CreateKeyBody(name="x", secret="sk", base_url="ftp://api.example.com")
    assert ei.value.status == 422
    assert ei.value.code == "VALIDATION_ERROR"

    body = CreateKeyBody(name="x", secret="sk", base_url="https://api.example.com/v1/")
    assert body.base_url == "https://api.example.com/v1"
    assert body.models_path is None and body.auth_scheme is None


def test_create_rejects_unknown_auth_scheme() -> None:
    async def scenario() -> None:
        session, svc, _ = await _session_and_service()
        try:
            with pytest.raises(AppError) as ei:
                await svc.create_key(
                    name="x",
                    secret="sk",
                    base_url="https://api.example.com/v1",
                    auth_scheme="basic",
                )
            assert ei.value.code == "VALIDATION_ERROR"
        finally:
            await session.close()

    _run(scenario)


def test_create_rejects_endpoint_plus_catalog_entry() -> None:
    async def scenario() -> None:
        session, svc, _ = await _session_and_service()
        try:
            with pytest.raises(AppError) as ei:
                await svc.create_key(
                    name="x",
                    secret="sk",
                    base_url="https://api.example.com/v1",
                    catalog_entry_id="ollama",
                )
            assert ei.value.code == "VALIDATION_ERROR"
        finally:
            await session.close()

    _run(scenario)


def test_create_rejects_endpoint_plus_provider() -> None:
    """The endpoint is the whole configuration — a provider dropdown value
    would contradict the entry that `base_url` resolves to."""
    async def scenario() -> None:
        session, svc, _ = await _session_and_service()
        try:
            with pytest.raises(AppError) as ei:
                await svc.create_key(
                    name="x",
                    secret="sk",
                    base_url="https://api.example.com/v1",
                    provider="cursor",
                )
            assert ei.value.code == "VALIDATION_ERROR"
        finally:
            await session.close()

    _run(scenario)


# ---------------------------------------------------------------------------
# list / get
# ---------------------------------------------------------------------------


def test_list_and_get_show_resolved_endpoint() -> None:
    async def scenario() -> None:
        session, svc, _ = await _session_and_service()
        try:
            created = await svc.create_key(
                name="Endpoint key",
                secret="sk-live",
                base_url="https://api.example.com/v1",
            )
            legacy = await svc.create_key(
                name="Legacy key",
                provider="cursor",
                api_kind="cursor_sdk",
                secret="cursor-secret",
            )

            listed = await svc.list_keys()
            by_id = {item["id"]: item for item in listed}
            assert by_id[created["id"]]["base_url"] == "https://api.example.com/v1"
            assert by_id[created["id"]]["models_path"] == "/models"
            assert by_id[created["id"]]["auth_scheme"] == "bearer"
            assert by_id[created["id"]]["has_secret"] is True

            # Legacy key with no catalog entry: the fields are present but null.
            assert by_id[legacy["id"]]["base_url"] is None
            assert by_id[legacy["id"]]["models_path"] is None
            assert by_id[legacy["id"]]["auth_scheme"] is None

            got = await svc.get_key(created["id"])
            assert got["base_url"] == "https://api.example.com/v1"
            assert got["catalog_entry_id"] == created["catalog_entry_id"]
            # Response shape is additive — nothing old was dropped.
            for field in (
                "id",
                "name",
                "provider",
                "api_kind",
                "owner_scope",
                "catalog_entry_id",
                "secret_ref_prefix",
                "has_secret",
                "status",
                "company_ids",
                "last_probe",
            ):
                assert field in got, field
            assert "secret" not in got
        finally:
            await session.close()

    _run(scenario)


def test_list_shows_endpoint_of_key_pointing_at_archived_entry() -> None:
    """A key may still point at an archived entry — the admin must still see
    where it goes rather than a blank endpoint."""

    async def scenario() -> None:
        from datetime import UTC, datetime

        session, svc, _ = await _session_and_service()
        try:
            created = await svc.create_key(
                name="Archived target",
                secret="sk-arch",
                base_url="https://old.example.net/v1",
            )
            entry = await _entry(session, created["catalog_entry_id"])
            assert entry is not None
            entry.archived_at = datetime.now(UTC)
            await session.commit()

            got = await svc.get_key(created["id"])
            assert got["base_url"] == "https://old.example.net/v1"
        finally:
            await session.close()

    _run(scenario)


# ---------------------------------------------------------------------------
# legacy create is untouched
# ---------------------------------------------------------------------------


def test_legacy_create_without_base_url_is_unchanged() -> None:
    async def scenario() -> None:
        session, svc, _ = await _session_and_service()
        try:
            created = await svc.create_key(
                name="Cursor pool",
                provider="cursor",
                api_kind="cursor_sdk",
                secret="cursor-secret-aaa",
                catalog_entry_id=None,
            )
            assert created["provider"] == "cursor"
            assert created["api_kind"] == "cursor_sdk"
            assert created["catalog_entry_id"] is None
            assert created["base_url"] is None
            assert created["has_secret"] is True
            # No catalog entry was synthesised for a legacy create.
            assert await _entries(session) == []

            row = await session.get(AiProviderKeyRow, created["id"])
            assert row is not None
            assert row.provider == "cursor"
            assert row.api_kind == "cursor_sdk"
        finally:
            await session.close()

    _run(scenario)


def test_legacy_create_with_catalog_entry_still_links_it() -> None:
    async def scenario() -> None:
        session, svc, _ = await _session_and_service()
        try:
            # Seeding happens through ensure_seeded on first catalog touch.
            await ensure_endpoint_entry(session, base_url="https://api.openai.com")
            created = await svc.create_key(
                name="OpenAI key",
                provider="codex",
                api_kind="openai_api",
                secret="sk-openai",
                catalog_entry_id="openai",
            )
            assert created["catalog_entry_id"] == "openai"
            assert created["base_url"] == "https://api.openai.com"
            assert created["models_path"] == "/v1/models"
            assert created["auth_scheme"] == "bearer"
        finally:
            await session.close()

    _run(scenario)


def test_legacy_create_defaults_provider_and_kind() -> None:
    """`provider`/`api_kind` are optional now; omitting them (a body that
    predates the endpoint change) still yields the old cursor/cursor_sdk key."""

    async def scenario() -> None:
        session, svc, _ = await _session_and_service()
        try:
            created = await svc.create_key(name="Default", secret="sk-default")
            assert created["provider"] == "cursor"
            assert created["api_kind"] == "cursor_sdk"
            assert created["catalog_entry_id"] is None
        finally:
            await session.close()

    _run(scenario)


# ---------------------------------------------------------------------------
# probe
# ---------------------------------------------------------------------------


def test_probe_resolves_endpoint_entry_and_calls_its_base_url() -> None:
    """A key created from an endpoint resolves to that entry, and the pod probe
    sends the request to that entry's base_url (not a catalog default)."""

    async def scenario() -> None:
        session, svc, secrets = await _session_and_service()
        try:
            created = await svc.create_key(
                name="Probed endpoint",
                secret="sk-probe-1",
                base_url="https://api.example.com/v1",
                auth_scheme="x-api-key",
            )
            row = await session.get(AiProviderKeyRow, created["id"])
            assert row is not None
            assert row.catalog_entry_id == created["catalog_entry_id"]

            # The resolver reads the key's catalog_entry_id first: the probe
            # must land on the entry created from the endpoint, not on some
            # other custom entry (ollama is seeded by then and is `custom`).
            from prodavan.application.ai_keys.probe.provider_resolver import ProviderResolver

            resolved = await ProviderResolver(session).resolve(
                api_kind=row.api_kind,
                provider=row.provider,
                secret="sk-probe-1",
                catalog_entry_id=row.catalog_entry_id,
            )
            assert resolved is not None
            assert resolved.catalog_id == created["catalog_entry_id"]
            assert resolved.base_url == "https://api.example.com/v1"
            assert resolved.auth_scheme == "x-api-key"
            from prodavan.application.ai_keys.probe.pod_probe_service import ProbePodService

            get_response = MagicMock()
            get_response.status_code = 200
            get_response.text = ""
            get_response.json = MagicMock(
                return_value={"data": [{"id": "cheap-fast"}, {"id": "cheap-pro"}]}
            )
            post_response = MagicMock()
            post_response.status_code = 201
            post_response.text = ""
            delete_response = MagicMock(status_code=204)

            client = AsyncMock()
            client.get = AsyncMock(return_value=get_response)
            client.post = AsyncMock(return_value=post_response)
            client.delete = AsyncMock(return_value=delete_response)
            client.__aenter__ = AsyncMock(return_value=client)
            client.__aexit__ = AsyncMock(return_value=None)
            client_cls = MagicMock(return_value=client)

            original = settings.pod_probe_enabled
            settings.pod_probe_enabled = True
            try:
                service = ProbePodService(session, secrets=secrets, http_client=client_cls)
                result = await service.probe_key(row)
            finally:
                settings.pod_probe_enabled = original

            assert result.status.value == "ok", result
            assert result.models == ["cheap-fast", "cheap-pro"]
            assert result.error_code is None

            client.get.assert_awaited_once()
            args, kwargs = client.get.await_args
            # The pod probe calls its own /v1/models and carries the provider
            # endpoint as params — the request that reaches the vendor is built
            # from these, so they must be the entry's values.
            assert str(args[0]).endswith("/v1/models")
            params = kwargs["params"]
            assert params["base_url"] == "https://api.example.com/v1"
            assert params["models_path"] == "/models"
            assert params["auth_scheme"] == "x-api-key"
            assert params["key_id"] == created["id"]

            # The secret was pushed as a lease for this key (not persisted).
            assert client.post.await_args.kwargs["json"]["key_id"] == created["id"]
        finally:
            await session.close()

    _run(scenario)


def test_probe_params_use_endpoint_paths_not_catalog_defaults() -> None:
    async def scenario() -> None:
        session, svc, secrets = await _session_and_service()
        try:
            created = await svc.create_key(
                name="Pathy",
                secret="sk-pathy",
                base_url="https://api.example.com/v1",
                models_path="/v1/models",
                auth_scheme="bearer",
            )
            row = await session.get(AiProviderKeyRow, created["id"])
            assert row is not None

            from prodavan.application.ai_keys.probe.pod_probe_service import ProbePodService

            response = MagicMock(status_code=200, text="")
            response.json = MagicMock(return_value={"models": ["m1"]})
            client = AsyncMock()
            client.get = AsyncMock(return_value=response)
            client.post = AsyncMock(return_value=MagicMock(status_code=201))
            client.delete = AsyncMock(return_value=MagicMock(status_code=204))
            client.__aenter__ = AsyncMock(return_value=client)
            client.__aexit__ = AsyncMock(return_value=None)

            original = settings.pod_probe_enabled
            settings.pod_probe_enabled = True
            try:
                result = await ProbePodService(
                    session, secrets=secrets, http_client=MagicMock(return_value=client)
                ).probe_key(row)
            finally:
                settings.pod_probe_enabled = original

            assert result.status.value == "ok"
            _args, kwargs = client.get.await_args
            # "/v1" base + "/v1/models" must not become "/v1/v1/models".
            assert kwargs["params"]["models_path"] == "/models"
        finally:
            await session.close()

    _run(scenario)


def test_reused_entry_rejects_contradicting_auth_scheme() -> None:
    """The endpoint entry is shared and authoritative: a second key may not ask
    for a different auth scheme on the same endpoint (it would fail to auth)."""

    async def scenario() -> None:
        session, svc, _ = await _session_and_service()
        try:
            await svc.create_key(
                name="First",
                secret="sk-1",
                base_url="https://api.example.com/v1",
                auth_scheme="x-api-key",
            )
            # Same settings on the same endpoint: reused, no conflict.
            same = await svc.create_key(
                name="Second",
                secret="sk-2",
                base_url="https://api.example.com/v1",
                auth_scheme="x-api-key",
                models_path="/models",
            )
            assert same["auth_scheme"] == "x-api-key"

            with pytest.raises(AppError) as ei:
                await svc.create_key(
                    name="Third",
                    secret="sk-3",
                    base_url="https://api.example.com/v1",
                    auth_scheme="bearer",
                )
            assert ei.value.code == "CONFLICT"
            assert ei.value.status == 409
            assert "auth_scheme" in ei.value.detail
        finally:
            await session.close()

    _run(scenario)


def test_reused_seed_rejects_contradicting_models_path() -> None:
    async def scenario() -> None:
        session, svc, _ = await _session_and_service()
        try:
            # Seeded openai entry: models_path /v1/models, auth bearer.
            ok = await svc.create_key(
                name="OpenAI",
                secret="sk-o",
                base_url="https://api.openai.com",
                models_path="/v1/models",
            )
            assert ok["models_path"] == "/v1/models"

            with pytest.raises(AppError) as ei:
                await svc.create_key(
                    name="OpenAI other",
                    secret="sk-o2",
                    base_url="https://api.openai.com",
                    models_path="/models",
                )
            assert ei.value.code == "CONFLICT"
        finally:
            await session.close()

    _run(scenario)


# ---------------------------------------------------------------------------
# patch: re-point a key at an endpoint
# ---------------------------------------------------------------------------


def test_patch_base_url_repoints_and_shares_entry() -> None:
    async def scenario() -> None:
        session, svc, _ = await _session_and_service()
        try:
            first = await svc.create_key(
                name="A", secret="sk-a", base_url="https://api.example.com/v1"
            )
            legacy = await svc.create_key(
                name="B", provider="codex", api_kind="openai_api", secret="sk-b"
            )
            patched = await svc.patch_key(legacy["id"], {"base_url": "https://API.example.com/v1/"})
            assert patched["catalog_entry_id"] == first["catalog_entry_id"]
            assert patched["base_url"] == "https://api.example.com/v1"
            assert patched["api_kind"] == "custom"
            assert patched["provider"] == "codex"

            row = await session.get(AiProviderKeyRow, legacy["id"])
            assert row is not None
            assert row.catalog_entry_id == first["catalog_entry_id"]
            target = base_url_key("https://api.example.com/v1")
            entries = [
                e
                for e in await _entries(session)
                if base_url_key(str(e.payload.get("base_url"))) == target
            ]
            assert len(entries) == 1
        finally:
            await session.close()

    _run(scenario)


def test_patch_path_without_base_url_is_rejected() -> None:
    async def scenario() -> None:
        session, svc, _ = await _session_and_service()
        try:
            created = await svc.create_key(
                name="A", secret="sk-a", base_url="https://api.example.com/v1"
            )
            with pytest.raises(AppError) as ei:
                await svc.patch_key(created["id"], {"models_path": "/v2/models"})
            assert ei.value.code == "VALIDATION_ERROR"
            assert ei.value.status == 422
            # Nothing was mutated.
            got = await svc.get_key(created["id"])
            assert got["models_path"] == "/models"
        finally:
            await session.close()

    _run(scenario)


def test_patch_base_url_with_provider_is_rejected() -> None:
    async def scenario() -> None:
        session, svc, _ = await _session_and_service()
        try:
            created = await svc.create_key(name="A", secret="sk-a")
            with pytest.raises(AppError) as ei:
                await svc.patch_key(
                    created["id"],
                    {"base_url": "https://api.example.com/v1", "provider": "cursor"},
                )
            assert ei.value.code == "VALIDATION_ERROR"
        finally:
            await session.close()

    _run(scenario)


def test_patch_legacy_fields_unchanged_without_endpoint() -> None:
    async def scenario() -> None:
        session, svc, _ = await _session_and_service()
        try:
            created = await svc.create_key(
                name="Legacy", provider="cursor", api_kind="cursor_sdk", secret="sk-old"
            )
            patched = await svc.patch_key(
                created["id"], {"api_kind": "openai_api", "provider": "codex"}
            )
            assert patched["api_kind"] == "openai_api"
            assert patched["provider"] == "codex"
            assert patched["base_url"] is None
            assert patched["catalog_entry_id"] is None
        finally:
            await session.close()

    _run(scenario)


# ---------------------------------------------------------------------------
# HTTP contract: the exact body the UI sends
# ---------------------------------------------------------------------------


def test_http_create_from_endpoint_and_list_response() -> None:
    """The UI sends only an endpoint + a key; the response shows the endpoint.

    Wires the real router with the admin/session dependencies overridden, so the
    field names in this test are the field names the UI must send.
    """
    from fastapi import FastAPI
    from httpx import ASGITransport, AsyncClient

    from prodavan.api.deps import get_db_session, require_platform_admin
    from prodavan.api.exception_handlers import register_exception_handlers
    from prodavan.api.v1.ai_keys import router as ai_keys_router
    from prodavan.domain.identity import Principal

    async def scenario() -> None:
        engine = create_async_engine("sqlite+aiosqlite://")
        async with engine.begin() as conn:
            await conn.run_sync(
                lambda sync: [t.__table__.create(sync, checkfirst=True) for t in _TABLES]
            )
        session = AsyncSession(engine, expire_on_commit=False)

        app = FastAPI()
        register_exception_handlers(app)
        app.include_router(ai_keys_router)
        app.dependency_overrides[require_platform_admin] = lambda: Principal(
            sub="admin-1", roles=frozenset({"platform.admin"})
        )
        app.dependency_overrides[get_db_session] = lambda: session

        transport = ASGITransport(app=app)
        try:
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                created_response = await client.post(
                    "/admin/ai-keys",
                    json={
                        "name": "CheapAI",
                        "secret": "sk-ui-1",
                        "base_url": "https://api.cheapai.example/v1",
                    },
                )
                assert created_response.status_code == 201, created_response.text
                created = created_response.json()
                assert created["base_url"] == "https://api.cheapai.example/v1"
                assert created["models_path"] == "/models"
                assert created["auth_scheme"] == "bearer"
                assert created["has_secret"] is True
                assert "secret" not in created

                listed = await client.get("/admin/ai-keys")
                assert listed.status_code == 200, listed.text
                item = next(k for k in listed.json() if k["id"] == created["id"])
                assert item["base_url"] == "https://api.cheapai.example/v1"
                assert item["catalog_entry_id"] == created["catalog_entry_id"]

                got = await client.get(f"/admin/ai-keys/{created['id']}")
                assert got.status_code == 200
                assert got.json()["base_url"] == "https://api.cheapai.example/v1"

                bad = await client.post(
                    "/admin/ai-keys",
                    json={"name": "Bad", "secret": "sk", "base_url": "ftp://x.example"},
                )
                assert bad.status_code == 422, bad.text
                assert bad.headers["content-type"].startswith("application/problem+json")
                assert bad.json()["code"] == "VALIDATION_ERROR"

                # Legacy body (no base_url) still works and reports a null endpoint.
                legacy = await client.post(
                    "/admin/ai-keys",
                    json={
                        "name": "Cursor",
                        "provider": "cursor",
                        "api_kind": "cursor_sdk",
                        "secret": "cursor-secret",
                    },
                )
                assert legacy.status_code == 201, legacy.text
                assert legacy.json()["base_url"] is None
                assert legacy.json()["provider"] == "cursor"
        finally:
            await session.close()
            await engine.dispose()

    _run(scenario)


# ---------------------------------------------------------------------------
# URL normalisation helpers
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("left", "right"),
    [
        ("https://api.example.com/v1", "https://API.Example.com/v1/"),
        ("https://api.example.com", "https://api.example.com/"),
        ("https://api.example.com:443/v1", "https://api.example.com/v1"),
        ("http://host:80", "http://host"),
        ("api.example.com/v1", "https://api.example.com/v1"),
    ],
)
def test_base_url_key_treats_endpoints_as_equal(left: str, right: str) -> None:
    assert base_url_key(left) == base_url_key(right)


def test_base_url_key_keeps_path_and_port_distinct() -> None:
    assert base_url_key("https://h/v1") != base_url_key("https://h/v2")
    assert base_url_key("http://h") != base_url_key("https://h")
    assert base_url_key("https://h:8443") != base_url_key("https://h")


def test_display_host_and_slug() -> None:
    assert display_host("https://api.example.com/v1") == "api.example.com"
    assert display_host("http://127.0.0.1:11434/v1") == "127.0.0.1:11434"
    assert display_host("https://api.example.com:443") == "api.example.com"
    assert slug_from_host("api.example.com") == "api-example-com"
    assert slug_from_host("127.0.0.1:11434") == "127-0-0-1-11434"
    assert slug_from_host("") == "endpoint"
