"""Unit tests — RelationsQuery / RelationsCommand / envelopes."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from prodavan.core.events.envelope import relation_event_envelope
from prodavan.domain.relations import RELATION_GRANTED, RelationKind


def test_relation_event_envelope_shape() -> None:
    env = relation_event_envelope(
        event_id="rel_1",
        event_type=RELATION_GRANTED,
        company_id="co_1",
        cabinet_id="cab_1",
        payload={"relation_kind": RelationKind.ASSIGNMENT},
    )
    assert env.bus == "relation_event"
    d = env.to_dict()
    assert d["event_type"] == RELATION_GRANTED
    assert d["payload"]["relation_kind"] == "assignment"


@pytest.mark.asyncio
async def test_kafka_topic_for_relation_event() -> None:
    from prodavan.core.infra.kafka_manager import KafkaManager

    mgr = KafkaManager(enabled=False, topic_relation_events="prodavan.relation.events")
    assert mgr.topic_for("relation_event") == "prodavan.relation.events"


@pytest.mark.asyncio
async def test_relations_command_schedules_envelope(monkeypatch: pytest.MonkeyPatch) -> None:
    from prodavan.application.relations.commands import RelationsCommand

    session = MagicMock()
    scheduled: list = []

    def _schedule(_session, envelope) -> None:
        scheduled.append(envelope)

    monkeypatch.setattr(
        "prodavan.core.events.deferred.schedule_envelope_publish",
        _schedule,
    )

    cmd = RelationsCommand(session)
    cmd._grants = MagicMock()
    cmd._grants.assign_employee = AsyncMock()

    await cmd.assign_employee_to_cabinet(
        cabinet_id="cab_1",
        company_id="co_1",
        employee_id="emp_1",
    )

    assert len(scheduled) == 1
    assert scheduled[0].bus == "relation_event"
    assert scheduled[0].event_type == RELATION_GRANTED
    assert scheduled[0].payload["relation_kind"] == RelationKind.ASSIGNMENT


@pytest.mark.asyncio
async def test_handle_relation_event_noop_for_grant() -> None:
    from prodavan.application.relations.commands import handle_relation_event_envelope

    env = relation_event_envelope(
        event_id="e1",
        event_type=RELATION_GRANTED,
        payload={"relation_kind": RelationKind.GRANT},
    )
    await handle_relation_event_envelope(env)
