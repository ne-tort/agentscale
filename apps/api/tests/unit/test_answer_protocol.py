from prodavan.application.agent.answer_protocol import (
    FINAL_ANSWER_CLOSE,
    FINAL_ANSWER_OPEN,
    FINAL_ANSWER_PROTOCOL,
    strip_final_answer_tags,
    with_final_answer_protocol,
)
from prodavan.application.agent.chat_projection import (
    assistant_text_from_events,
    events_to_chat_blocks,
)


def test_protocol_is_prepended_to_the_user_message() -> None:
    wrapped = with_final_answer_protocol("собери отчёт")
    assert wrapped.startswith(FINAL_ANSWER_PROTOCOL)
    assert wrapped.endswith("собери отчёт")
    assert FINAL_ANSWER_OPEN in wrapped


def test_protocol_is_idempotent() -> None:
    once = with_final_answer_protocol("привет")
    assert with_final_answer_protocol(once) == once


def test_rehydrated_history_with_a_tag_still_gets_the_contract() -> None:
    history = f"assistant: {FINAL_ANSWER_OPEN}прошлый ответ</final_answer>\nuser: новый"
    wrapped = with_final_answer_protocol(history)
    assert wrapped.startswith("Правила финального ответа:")
    assert history in wrapped


def test_empty_message_is_untouched() -> None:
    assert with_final_answer_protocol("") == ""
    assert with_final_answer_protocol("   ") == "   "


def test_strip_removes_tags_for_plain_text_consumers() -> None:
    assert (
        strip_final_answer_tags(f"{FINAL_ANSWER_OPEN}ответ{FINAL_ANSWER_CLOSE}")
        == "ответ"
    )


def test_chat_blocks_keep_the_tag_for_the_turn_builder() -> None:
    events = [
        {
            "seq": 1,
            "type": "text_delta",
            "data": {"text": f"{FINAL_ANSWER_OPEN}| a | b |</final_answer>"},
        }
    ]
    blocks = events_to_chat_blocks(events)
    assert FINAL_ANSWER_OPEN in blocks[0]["text"]
    # Plain-text consumers (webhooks / rehydrate) get the stripped text.
    assert assistant_text_from_events(events) == "| a | b |"
