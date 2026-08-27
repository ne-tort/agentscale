"""Unit tests — Companies soft-delete publishes Auth delete + enqueues cascade."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from prodavan.application.companies.service import CompaniesCommandService
from prodavan.domain.errors import AppError
from prodavan.domain.identity import Principal


@pytest.mark.asyncio
async def test_soft_delete_publishes_and_enqueues(monkeypatch: pytest.MonkeyPatch) -> None:
    company = SimpleNamespace(
        id="co_1",
        name="Acme",
        keycloak_sub="kc_co",
        deleted_at=None,
    )
    session = MagicMock()
    session.get = AsyncMock(return_value=company)
    session.commit = AsyncMock()

    emitted: list[dict] = []

    class _FakeEvents:
        def __init__(self, _session: object) -> None:
            pass

        async def emit(self, **kwargs: object) -> None:
            emitted.append(dict(kwargs))

    published: list[dict] = []
    enqueued: list[dict] = []

    async def _publish_delete(**kwargs: object) -> bool:
        published.append(dict(kwargs))
        return True

    def _enqueue(company_id: str, *, actor_sub: str = "system") -> dict:
        enqueued.append({"company_id": company_id, "actor_sub": actor_sub})
        return {"enqueued": True, "company_id": company_id}

    monkeypatch.setattr(
        "prodavan.application.projects.platform_event_service.PlatformEventService",
        _FakeEvents,
    )
    monkeypatch.setattr(
        "prodavan.application.auth.lifecycle.publish_delete_command",
        _publish_delete,
    )
    monkeypatch.setattr(
        "prodavan.core.jobs.enqueue.enqueue_cascade_company_deleted",
        _enqueue,
    )

    out = await CompaniesCommandService(session).soft_delete(
        "co_1", principal=Principal(sub="admin")
    )
    assert out["deleted"] is True
    assert out["soft"] is True
    assert out["cascade_enqueued"] is True
    assert company.deleted_at is not None
    assert emitted[0]["event_type"] == "company.deleted"
    assert published[0]["client_ref"] == "company:co_1"
    assert published[0]["sub"] == "kc_co"
    assert enqueued[0]["company_id"] == "co_1"


@pytest.mark.asyncio
async def test_soft_delete_already_deleted_is_404() -> None:
    company = SimpleNamespace(
        id="co_1",
        name="Acme",
        keycloak_sub=None,
        deleted_at=datetime.now(UTC),
    )
    session = MagicMock()
    session.get = AsyncMock(return_value=company)
    with pytest.raises(AppError) as exc:
        await CompaniesCommandService(session).soft_delete(
            "co_1", principal=Principal(sub="admin")
        )
    assert exc.value.status == 404
