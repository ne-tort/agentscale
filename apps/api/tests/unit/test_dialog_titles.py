"""Default multi-chat titles: Диалог / Диалог N; first message does not rename."""

from types import SimpleNamespace

from prodavan.application.agent.session_service import (
    _default_chat_title,
    _dialog_title_for_ordinal,
    _touch_session_activity,
)


def test_dialog_title_for_ordinal() -> None:
    assert _dialog_title_for_ordinal(1) == "Диалог"
    assert _dialog_title_for_ordinal(0) == "Диалог"
    assert _dialog_title_for_ordinal(2) == "Диалог 2"
    assert _dialog_title_for_ordinal(5) == "Диалог 5"


def test_default_chat_title_empty_is_dialog() -> None:
    assert _default_chat_title("") == "Диалог"
    assert _default_chat_title("   ") == "Диалог"


def test_touch_does_not_overwrite_existing_dialog_title() -> None:
    row = SimpleNamespace(title="Диалог", last_message_at=None)
    _touch_session_activity(row, text="первое сообщение пользователя")
    assert row.title == "Диалог"

    row2 = SimpleNamespace(title="Диалог 3", last_message_at=None)
    _touch_session_activity(row2, text="hello")
    assert row2.title == "Диалог 3"


def test_touch_sets_title_only_when_missing() -> None:
    row = SimpleNamespace(title=None, last_message_at=None)
    _touch_session_activity(row, text="короткий заголовок")
    assert row.title == "короткий заголовок"
