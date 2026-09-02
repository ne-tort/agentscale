"""Unit tests — cumulative text delta normalization."""

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


def test_overlap_merge_at_word_boundary() -> None:
    inc, cum = normalize_text_delta("Проверка", "роверка прошла")
    assert inc == " прошла"
    assert cum == "Проверка прошла"


def test_overlap_merge_cascade() -> None:
    _, cum = normalize_text_delta("Проверка", "роверка прошла")
    inc, cum = normalize_text_delta(cum, " успешно")
    assert inc == " успешно"
    assert cum == "Проверка прошла успешно"
