"""Лимит шагов (turns) на один ход агента.

Термины: «шаг» = одна итерация цикла модели (model call + исполнение её
tool-call'ов). Рантайм крутит цикл `for turn in range(maxTurns)` и по
исчерпании останавливается с `done.reason = "max_turns"`.

Приоритет в рантайме (`turnLimitsFromConfig`):
`send.max_turns ?? config.runtime.max_turns ?? defaultMaxTurns`.
Схема send требует ПОЛОЖИТЕЛЬНОЕ целое, поэтому «без ограничений» нельзя
выразить нулём или null на проводе — только большим значением.
"""

from __future__ import annotations

#: «Без ограничений» для `.prodavan/config.yaml`. Рантайму нужно положительное
#: целое, поэтому снимаем ограничение практически, но оставляем конечный
#: потолок как защиту от бесконечного цикла tool-call'ов. Для сравнения:
#: типичный содержательный ход агента — десятки шагов, не сотни.
UNLIMITED_MAX_TURNS = 1000

#: Потолок пользовательской настройки: больше не имеет смысла (это уже
#: «без ограничений») и не даёт сохранить абсурдное значение.
MAX_TURNS_CEILING = UNLIMITED_MAX_TURNS

#: `done.reason`, которым рантайм сообщает об исчерпании лимита шагов
#: (`query-loop.ts`: `yield* finish("max_turns", { turns: maxTurns })`).
DONE_REASON_MAX_TURNS = "max_turns"


def normalize_max_turns(raw: int | None) -> int | None:
    """Значение настройки чата: None = без ограничений, иначе 1..потолок.

    0 и отрицательные трактуем как «снять лимит», а не как ошибку: в UI это
    состояние переключателя «Ограничить количество шагов за раз» = выкл.
    """
    if raw is None:
        return None
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return None
    if value <= 0:
        return None
    return min(value, MAX_TURNS_CEILING)


def resolve_send_max_turns(session_max_turns: int | None) -> int | None:
    """Что класть в тело send: None — не класть вовсе (возьмётся из конфига).

    Явно отправляем только когда пользователь задал лимит: так платформенная
    политика и конфиг проекта остаются потолком по умолчанию, а настройка чата
    работает как ЕГО УЖЕСТОЧЕНИЕ, а не обход.
    """
    return normalize_max_turns(session_max_turns)
