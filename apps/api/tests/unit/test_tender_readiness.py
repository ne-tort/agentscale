"""Готовность тендера к отправке КП: пороги 70% / 90% и распознавание условий.

Правило: отчёт уходит, когда подобрано >=70% позиций; если заказчик требует
предложение по ВСЕМ позициям — порог 90% (частичное КП там отклонили бы).
Условие ищется в текстах заказчика (comment/description/payment_terms), а не
в метаданных площадки.
"""

from __future__ import annotations

from prodavan.application.modules.equipment_tender_readiness import (
    ALL_POSITIONS_THRESHOLD,
    DEFAULT_THRESHOLD,
    assess_readiness,
    coverage,
    customer_texts,
    requires_all_positions,
)


def test_thresholds() -> None:
    assert DEFAULT_THRESHOLD == 0.70
    assert ALL_POSITIONS_THRESHOLD == 0.90


def test_coverage_basic() -> None:
    assert coverage(lines_total=10, lines_with_candidates=7) == 0.7
    assert coverage(lines_total=10, lines_with_candidates=10) == 1.0
    assert coverage(lines_total=10, lines_with_candidates=0) == 0.0


def test_coverage_guards() -> None:
    assert coverage(lines_total=0, lines_with_candidates=5) == 0.0
    # больше кандидатов, чем позиций — не выходим за 1.0
    assert coverage(lines_total=5, lines_with_candidates=9) == 1.0
    assert coverage(lines_total=5, lines_with_candidates=-3) == 0.0


# ------------------------------------------------------- условия заказчика

def test_requires_all_positions_positive() -> None:
    cases = [
        "Предложение подаётся по всем позициям лота.",
        "Требуется полное предложение по всем позициям.",
        "Частичное предложение не рассматривается.",
        "Предложение должно содержать все позиции обязательно.",
        "Не допускается частичное предложение.",
    ]
    for text in cases:
        ok, evidence = requires_all_positions({"comment": text})
        assert ok is True, text
        assert evidence, text


def test_requires_all_positions_negative() -> None:
    cases = [
        "Оплата 100% в течение 30 дней с момента поставки товара.",
        "Поставка до склада покупателя, гарантия 12 месяцев.",
        "Допускается частичное предложение по позициям.",
        "",
    ]
    for text in cases:
        ok, evidence = requires_all_positions({"comment": text})
        assert ok is False, text
        assert evidence is None, text


def test_requires_all_positions_checks_other_customer_fields() -> None:
    ok, _ = requires_all_positions({"description": "Подача по всем позициям обязательна"})
    assert ok is True
    ok, _ = requires_all_positions({"payment_terms": "Предложение по всем позициям"})
    assert ok is True


def test_customer_texts_skips_empty_and_metadata() -> None:
    body = {
        "comment": "Условия",
        "description": "  ",
        "payment_terms": "Оплата",
        "name": "это метаданные площадки, не текст заказчика",
        "number": "12345",
    }
    texts = customer_texts(body)
    assert texts == ["Условия", "Оплата"]


# --------------------------------------------------------------- readiness

def test_ready_at_70_percent_default() -> None:
    r = assess_readiness({}, lines_total=10, lines_with_candidates=7)
    assert r["threshold"] == 0.70
    assert r["coverage_percent"] == 70.0
    assert r["ready"] is True
    assert r["requires_all_positions"] is False
    assert r["lines_missing"] == 3


def test_not_ready_below_70() -> None:
    r = assess_readiness({}, lines_total=10, lines_with_candidates=6)
    assert r["ready"] is False
    assert r["lines_needed_to_pass"] == 1


def test_all_positions_raises_threshold_to_90() -> None:
    body = {"comment": "Предложение подаётся по всем позициям."}
    # 80% хватает для обычного тендера, но не для «по всем позициям»
    ok_default = assess_readiness({}, lines_total=10, lines_with_candidates=8)
    strict = assess_readiness(body, lines_total=10, lines_with_candidates=8)
    assert ok_default["ready"] is True
    assert strict["requires_all_positions"] is True
    assert strict["threshold"] == 0.90
    assert strict["ready"] is False
    assert strict["lines_needed_to_pass"] == 1


def test_all_positions_ready_at_90() -> None:
    body = {"comment": "По всем позициям обязательно."}
    r = assess_readiness(body, lines_total=10, lines_with_candidates=9)
    assert r["ready"] is True
    assert r["coverage_percent"] == 90.0


def test_zero_lines_is_not_ready() -> None:
    r = assess_readiness({}, lines_total=0, lines_with_candidates=0)
    assert r["ready"] is False
    assert r["coverage"] == 0.0


def test_evidence_returned_for_strict_tender() -> None:
    r = assess_readiness(
        {"comment": "ВНИМАНИЕ! Предложение подаётся по всем позициям лота без исключений."},
        lines_total=5,
        lines_with_candidates=5,
    )
    assert r["all_positions_evidence"]
    assert "всем позициям" in r["all_positions_evidence"]


def test_lines_needed_rounds_up() -> None:
    # 70% от 7 = 4.9 → нужно 5 позиций
    r = assess_readiness({}, lines_total=7, lines_with_candidates=4)
    assert r["ready"] is False
    assert r["lines_needed_to_pass"] == 1
    r2 = assess_readiness({}, lines_total=7, lines_with_candidates=0)
    assert r2["lines_needed_to_pass"] == 5
