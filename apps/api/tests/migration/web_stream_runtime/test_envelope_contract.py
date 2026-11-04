"""Regression: application envelope construction must not assume an enum member.

``ApiModel`` sets ``use_enum_values=True``, so a validated ``RunStreamEvent.type``
is the plain string value (``"run_started"``), not a ``RunEventType`` member.
Reading ``.value`` off it raised ``AttributeError`` on the very first canonical
SSE frame — on the live ``WebFacadeAdapter.run_chat`` path, not only in tests.

These tests pin both shapes of the input (string and enum member), for both the
application envelope and the wire adapter, plus the encoder that sits between
them. A future config change in either direction must keep all of this green.
"""

from __future__ import annotations

import pytest

from prodavan.application.web_api.contracts import SseEventEnvelope as WireSseEventEnvelope
from prodavan.application.web_facade.contracts import (
    RunEventType,
    RunStreamEvent,
    SseEventEnvelope,
    event_type_name,
)
from prodavan.application.web_facade_adapter.envelopes import (
    EncodedWireEvent,
    StreamIdentityError,
    StreamScope,
    WireEventEncoder,
)

from .acceptance_fixtures import PROJECT, RUN, SESSION

SCOPE = StreamScope(project_id=PROJECT, session_id=SESSION)


class _Index:
    """Single-run trusted index double: membership is explicit."""

    project_id = PROJECT
    session_id = SESSION
    run_id = RUN

    def __init__(self, sequences: set[int]) -> None:
        self._sequences = sequences

    def has(self, run_id: str, sequence: int) -> bool:
        return run_id == RUN and sequence in self._sequences

    def find_by_event_id(self, event_id: object) -> tuple[str, int] | None:
        from prodavan.application.web_api.contracts import uuid5_from_run

        for sequence in self._sequences:
            if uuid5_from_run(RUN, sequence) == event_id:
                return RUN, sequence
        return None


def _validated(seq: int = 1) -> RunStreamEvent:
    """A normally constructed (i.e. validated) event: ``type`` is a string."""
    return RunStreamEvent(type=RunEventType.STARTED, run_id=RUN, seq=seq)


def _constructed(seq: int = 1) -> RunStreamEvent:
    """An event whose ``type`` really is still the enum member."""
    return RunStreamEvent.model_construct(
        type=RunEventType.STARTED, run_id=RUN, seq=seq, text=None, block=None, data={}
    )


def test_validated_event_carries_the_string_value() -> None:
    """Guard the premise: if ``use_enum_values`` ever changes, this fails loudly."""
    assert _validated().type == "run_started"
    assert isinstance(_validated().type, str)


@pytest.mark.parametrize("build", [_validated, _constructed])
def test_envelope_from_run_event_accepts_string_and_enum_type(build) -> None:
    envelope = SseEventEnvelope.from_run_event(build())

    assert envelope.id == f"{RUN}:1"
    assert envelope.event == "run_started"
    assert envelope.model_dump(mode="json")["event"] == "run_started"


@pytest.mark.parametrize("build", [_validated, _constructed])
def test_wire_adapter_accepts_string_and_enum_type(build) -> None:
    wire = WireSseEventEnvelope.from_facade_event(build(), project_id=PROJECT, session_id=SESSION)

    assert wire.event_type.value == "run_started"
    assert wire.sequence == 1


@pytest.mark.parametrize("build", [_validated, _constructed])
def test_encoder_emits_the_canonical_frame_for_both_shapes(build) -> None:
    envelope = SseEventEnvelope.from_run_event(build())
    encoded = WireEventEncoder(_Index({1})).encode(envelope, scope=SCOPE)

    assert isinstance(encoded, EncodedWireEvent)
    assert encoded.event_name == "run_started"
    assert '"event_type":"run_started"' in encoded.data


@pytest.mark.parametrize("value", [None, "", "   ", 7, object()])
def test_event_type_name_refuses_non_canonical_input(value: object) -> None:
    with pytest.raises(ValueError):
        event_type_name(value)  # type: ignore[arg-type]


def test_encoder_refuses_an_event_without_a_wire_type() -> None:
    """An uncanonical type is a 502 refusal, never an ``AttributeError`` → 500."""
    event = RunStreamEvent.model_construct(
        type=object(), run_id=RUN, seq=1, text=None, block=None, data={}
    )
    envelope = SseEventEnvelope(id=f"{RUN}:1", event="run_started", data=event)

    with pytest.raises(StreamIdentityError) as caught:
        WireEventEncoder(_Index({1})).encode(envelope, scope=SCOPE)

    assert caught.value.code == "UPSTREAM_STREAM_MALFORMED"
