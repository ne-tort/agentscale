"""Готовность тендера к отправке КП: полнота подбора позиций.

Правило продукта: отчёт по тендеру отправляется заказчику только когда подбор
покрывает достаточно позиций. Порог зависит от того, есть ли у заказчика
требование подавать предложение по ВСЕМ позициям:

- обычный тендер (можно частичное предложение) — **70%** позиций;
- тендер с требованием «по всем позициям» — **90%** (частичное КП там
  отклонили бы, поэтому отправлять почти-полный набор бессмысленно).

Требование «все позиции» ищется в текстах, которые пишет сам заказчик
(`comment` — условия процедуры, `description`, `payment_terms`), а не в
метаданных площадки. Кравлер отдаёт их в теле тендера (см.
`agentscale_push.tender_to_agentscale`), поэтому агент видит условия сразу
в кикофф-сообщении и не ходит за ними на площадку.
"""

from __future__ import annotations

import re
from typing import Any

# Пороги: обычный тендер и тендер с требованием подачи по всем позициям.
DEFAULT_THRESHOLD = 0.70
ALL_POSITIONS_THRESHOLD = 0.90

#: маркеры требования «предложение должно быть по всем позициям».
#: Ищем по нормализованному (нижний регистр, схлопнутые пробелы) тексту.
_ALL_POSITIONS_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"по\s+всем\s+позици"),
    re.compile(r"все\s+позиции\s+(?:обязательн|должн)"),
    re.compile(r"обязательн\w*\s+по\s+всем"),
    re.compile(r"предложени\w*\s+по\s+всем"),
    re.compile(r"полное\s+предложени"),
    re.compile(r"частичн\w*\s+(?:предложени|оферт|заявк)\w*\s+не"),
    re.compile(r"не\s+допуска\w*\s+частичн"),
    re.compile(r"только\s+полное\s+соответствие"),
    re.compile(r"каждой\s+позиции\s+обязательн"),
    re.compile(r"по\s+каждой\s+позиции\s+обязательн"),
    re.compile(r"all\s+(?:items|positions)\s+(?:must|are\s+required)"),
    re.compile(r"partial\s+(?:offer|bid)s?\s+(?:is|are)\s+not"),
)

#: тексты заказчика, где может быть условие подачи (не метаданные площадки).
CUSTOMER_TEXT_FIELDS = ("comment", "description", "payment_terms", "note")


def _norm(text: str) -> str:
    """Нормализация под поиск: нижний регистр, единые пробелы, ё→е."""
    return re.sub(r"\s+", " ", (text or "").replace("ё", "е").replace("Ё", "Е")).lower()


def customer_texts(body: dict[str, Any]) -> list[str]:
    """Непустые тексты заказчика из тела тендера (в порядке приоритета)."""
    out: list[str] = []
    for field in CUSTOMER_TEXT_FIELDS:
        raw = body.get(field)
        if isinstance(raw, str) and raw.strip():
            out.append(raw.strip())
    return out


def requires_all_positions(body: dict[str, Any]) -> tuple[bool, str | None]:
    """Есть ли требование подавать предложение по всем позициям.

    Возвращает (флаг, фрагмент-подтверждение). Фрагмент нужен, чтобы агент
    мог показать менеджеру, почему порог поднят до 90%.
    """
    for text in customer_texts(body):
        norm = _norm(text)
        for pattern in _ALL_POSITIONS_PATTERNS:
            m = pattern.search(norm)
            if m:
                start = max(0, m.start() - 60)
                end = min(len(norm), m.end() + 60)
                return True, norm[start:end].strip()
    return False, None


def coverage(
    *,
    lines_total: int,
    lines_with_candidates: int,
) -> float:
    """Доля позиций, по которым есть хотя бы один кандидат (0..1)."""
    if lines_total <= 0:
        return 0.0
    covered = max(0, min(lines_with_candidates, lines_total))
    return covered / lines_total


def assess_readiness(
    body: dict[str, Any],
    *,
    lines_total: int,
    lines_with_candidates: int,
    lines_with_offers: int = 0,
) -> dict[str, Any]:
    """Оценить готовность тендера к отправке КП.

    ``lines_with_candidates`` — позиции, где найден хотя бы один кандидат
    (found_groups); ``lines_with_offers`` — где материализовались офферы с
    ценой (используется как «сильнее» доказательство, если передан).
    """
    needs_all, evidence = requires_all_positions(body)
    threshold = ALL_POSITIONS_THRESHOLD if needs_all else DEFAULT_THRESHOLD
    covered = max(0, lines_with_candidates)
    ratio = coverage(lines_total=lines_total, lines_with_candidates=covered)
    missing = max(0, lines_total - covered)
    # сколько ещё позиций нужно закрыть, чтобы достичь порога
    need_count = 0
    if lines_total > 0 and ratio < threshold:
        import math

        need_count = max(0, math.ceil(threshold * lines_total) - covered)
    return {
        "lines_total": lines_total,
        "lines_covered": covered,
        "lines_with_offers": max(0, lines_with_offers),
        "lines_missing": missing,
        "coverage": round(ratio, 4),
        "coverage_percent": round(ratio * 100, 1),
        "threshold": threshold,
        "threshold_percent": round(threshold * 100, 1),
        "requires_all_positions": needs_all,
        "all_positions_evidence": evidence,
        "ready": lines_total > 0 and ratio >= threshold,
        "lines_needed_to_pass": need_count,
    }
