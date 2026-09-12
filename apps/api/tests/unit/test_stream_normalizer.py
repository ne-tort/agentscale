"""Unit tests — turn stream normalizer at agent ingress."""

from prodavan.application.agent.stream_normalizer import TurnStreamNormalizer
from prodavan.domain.agent import AgentEvent, AgentEventType


def test_normalizer_converts_cumulative_text_to_incremental() -> None:
    normalizer = TurnStreamNormalizer()
    out: list[str] = []
    for chunk in ["При", "Привет", "Привет!"]:
        event = normalizer.normalize_event(AgentEvent.now(AgentEventType.TEXT_DELTA, {"text": chunk}))
        if event is not None:
            out.append(str(event.data["text"]))
    assert "".join(out) == "Привет!"


def test_normalizer_skips_empty_incremental() -> None:
    normalizer = TurnStreamNormalizer()
    first = normalizer.normalize_event(AgentEvent.now(AgentEventType.TEXT_DELTA, {"text": "Hello"}))
    assert first is not None
    repeat = normalizer.normalize_event(AgentEvent.now(AgentEventType.TEXT_DELTA, {"text": "Hello"}))
    assert repeat is None


def test_normalizer_passes_through_non_delta_events() -> None:
    normalizer = TurnStreamNormalizer()
    done = normalizer.normalize_event(AgentEvent.now(AgentEventType.DONE, {"reason": "completed"}))
    assert done is not None
    assert done.type == AgentEventType.DONE


def test_normalizer_incremental_tokens_not_swallowed() -> None:
    """Incremental pieces that share a suffix/prefix must not be overlap-stripped."""
    normalizer = TurnStreamNormalizer()
    out: list[str] = []
    for chunk in ["Найденные", " проблемы", " конфигурации"]:
        event = normalizer.normalize_event(AgentEvent.now(AgentEventType.TEXT_DELTA, {"text": chunk}))
        if event is not None:
            out.append(str(event.data["text"]))
    assert "".join(out) == "Найденные проблемы конфигурации"


def test_normalizer_does_not_drop_shared_syllable_incremental() -> None:
    normalizer = TurnStreamNormalizer()
    # After "конф", an incremental "ден" (new text) must stay — not treated as overlap.
    # Cumulative "конфигурацию" still works via startswith.
    e1 = normalizer.normalize_event(AgentEvent.now(AgentEventType.TEXT_DELTA, {"text": "конф"}))
    assert e1 is not None and e1.data["text"] == "конф"
    e2 = normalizer.normalize_event(
        AgentEvent.now(AgentEventType.TEXT_DELTA, {"text": "конфигурацию"})
    )
    assert e2 is not None and e2.data["text"] == "игурацию"


def test_normalizer_resets_text_on_tool_call() -> None:
    normalizer = TurnStreamNormalizer()
    normalizer.normalize_event(AgentEvent.now(AgentEventType.TEXT_DELTA, {"text": "Hello"}))
    normalizer.normalize_event(
        AgentEvent.now(AgentEventType.TOOL_CALL, {"id": "t1", "name": "Read", "input": {}})
    )
    event = normalizer.normalize_event(AgentEvent.now(AgentEventType.TEXT_DELTA, {"text": "World"}))
    assert event is not None
    assert event.data["text"] == "World"
