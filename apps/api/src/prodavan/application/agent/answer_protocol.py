"""Final-answer protocol for the pod agent runtime.

The chat UI renders one collapsed "work" block per turn plus exactly one
final answer.  Guessing the final answer from message order is unreliable:
an agent can write a summary, then call one more tool, or split a table with
a tool call.  The runtime therefore gets an explicit, machine-readable
contract: the user-facing answer must be wrapped in ``<final_answer>`` tags.

The API persists the raw text (tags included); the transcript projection and
the Flutter turn builder strip the tags and make the answer a separate,
never-collapsed block.  Turns from older/newer runtimes without tags fall back
to the last assistant message.
"""

from __future__ import annotations

FINAL_ANSWER_OPEN = "<final_answer>"
FINAL_ANSWER_CLOSE = "</final_answer>"

FINAL_ANSWER_PROTOCOL = (
    "Правила финального ответа:\n"
    "1. Всю работу (вызовы инструментов, проверки, промежуточные рассуждения) "
    "выполняй до финального ответа.\n"
    f"2. Финальный ответ пользователю оберни в тег "
    f"{FINAL_ANSWER_OPEN}...{FINAL_ANSWER_CLOSE}.\n"
    "3. Внутри тега — только итог для пользователя: без служебных логов, "
    "плана и фраз «сейчас сделаю». Markdown разрешён (таблицы, списки, код).\n"
    f"4. Не вызывай инструменты внутри {FINAL_ANSWER_OPEN}. Если нужен ещё "
    "инструмент — сначала вызови его, затем открой тег.\n"
    f"5. Не повторяй тег и не выводи несколько финальных ответов: ровно один "
    f"{FINAL_ANSWER_OPEN} блок на ход."
)


def with_final_answer_protocol(text: str) -> str:
    """Prepend the answer-format contract to a runtime message.

    Idempotent only when the contract itself is already at the head: a
    rehydrated history may contain ``<final_answer>`` from previous turns and
    must still receive the contract for the new turn.
    """
    if not text or not text.strip():
        return text
    if text.lstrip().startswith("Правила финального ответа:"):
        return text
    return f"{FINAL_ANSWER_PROTOCOL}\n\n{text}"


def strip_final_answer_tags(text: str) -> str:
    """Remove the protocol tags for consumers that only need plain text.

    Chat blocks keep the tags until the Flutter turn builder separates work
    from the answer; webhooks, rehydrated history and plain transcript bubbles
    must never leak the markers.
    """
    if not text:
        return text
    return text.replace(FINAL_ANSWER_OPEN, "").replace(FINAL_ANSWER_CLOSE, "").strip()
