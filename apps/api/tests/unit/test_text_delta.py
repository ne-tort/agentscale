"""Unit tests — text delta normalization (cumulative vs incremental)."""

from prodavan.application.agent.text_delta import normalize_text_delta


def test_incremental_chunks_append() -> None:
    prev = ""
    inc, cum = normalize_text_delta(prev, "Hel")
    assert inc == "Hel"
    assert cum == "Hel"
    inc, cum = normalize_text_delta(cum, "lo")
    assert inc == "lo"
    assert cum == "Hello"


def test_cumulative_chunks_dedupe() -> None:
    prev = ""
    inc, cum = normalize_text_delta(prev, "При")
    assert inc == "При"
    assert cum == "При"
    inc, cum = normalize_text_delta(cum, "Привет")
    assert inc == "вет"
    assert cum == "Привет"
    inc, cum = normalize_text_delta(cum, "Привет!")
    assert inc == "!"
    assert cum == "Привет!"


def test_empty_chunk_keeps_previous() -> None:
    assert normalize_text_delta("Hello", "") == ("", "Hello")


def test_shorter_cumulative_is_ignored() -> None:
    assert normalize_text_delta("Привет!", "Привет") == ("", "Привет!")


def test_incremental_does_not_swallow_overlapping_prefix() -> None:
    """Regression: overlap merge used to drop leading syllables on incremental wire."""
    inc, cum = normalize_text_delta("конф", "конфигурацию")
    # Cumulative extension still works via startswith.
    assert inc == "игурацию"
    assert cum == "конфигурацию"

    # True incremental token that happens to share letters with the tail.
    inc, cum = normalize_text_delta("Найденные", "денные")
    assert inc == "денные"
    assert cum == "Найденныеденные"

    inc, cum = normalize_text_delta("подбор", "подбору")
    assert inc == "у"  # cumulative-style extension
    assert cum == "подбору"


def test_incremental_cyrillic_tokens_append() -> None:
    prev = ""
    for piece in ["кон", "фиг", "урацию"]:
        inc, prev = normalize_text_delta(prev, piece)
        assert inc == piece
    assert prev == "конфигурацию"
