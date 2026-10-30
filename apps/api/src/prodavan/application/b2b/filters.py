"""Category/relevance filtering for B2B-Center cards.

The supplier API has no tags; real structural signals are procedure status,
deadline, ОКПД2/ОКВЭД2 of lots and position/name keywords. Categories combine
these signals, so "only equipment/IT tenders" is one explicit config value
instead of a pile of ad-hoc filters.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

from .dto import B2BCard

# Selected by `B2B_CENTER_CATEGORIES` (empty = all rules). Rules are OR within
# a category and OR between categories.
CATEGORY_RULES: dict[str, dict[str, list[str]]] = {
    "it": {
        "keywords": [
            "сервер",
            "ноутбук",
            "компьютер",
            "моноблок",
            "монитор",
            "процессор",
            "память",
            "сетев",
            "коммутатор",
            "маршрутизатор",
            "программ",
            "лиценз",
            "сзи",
            "ит-",
            "ит ",
            "вычислительн",
        ],
        "okpd": ["26.2", "26.3", "26.8", "62", "63"],
    },
    "power": {
        "keywords": ["ибп", "электр", "кабел", "трансформатор", "генератор", "щит", "розет"],
        "okpd": ["27", "28"],
    },
    "office": {
        "keywords": ["мебел", "кресл", "стол", "шкаф", "канцеляр", "бумаг", "принтер", "картридж"],
        "okpd": ["31", "17", "22.2"],
    },
    "construction": {
        "keywords": ["строитель", "ремонт", "монтаж", "труб", "бетон", "кровл"],
        "okpd": ["23", "25", "41", "42", "43"],
    },
    "medical": {
        "keywords": ["медицин", "аппарат", "расходн", "диагност"],
        "okpd": ["32.5"],
    },
    "auto": {
        "keywords": ["автомоб", "шин", "запчаст", "спецтехник"],
        "okpd": ["29", "45"],
    },
}

TERMINAL_STATUSES = frozenset({"archived", "canceled", "cancelled", "deleted"})


@dataclass
class FilterDecision:
    matched: bool
    category: str = ""
    reasons: list[str] = field(default_factory=list)


class B2BCategoryFilter:
    def __init__(
        self,
        *,
        categories: list[str] | None = None,
        min_remaining_hours: float = 24.0,
        skip_not_actual: bool = True,
        skip_not_open: bool = False,
        keywords_include: list[str] | None = None,
        keywords_exclude: list[str] | None = None,
    ) -> None:
        requested = [c.strip() for c in (categories or []) if c.strip()]
        unknown = [c for c in requested if c not in CATEGORY_RULES]
        if unknown:
            raise ValueError(f"unknown B2B categories: {unknown}")
        self._selected = requested or list(CATEGORY_RULES)
        self._min_remaining_hours = max(0.0, min_remaining_hours)
        self._skip_not_actual = skip_not_actual
        self._skip_not_open = skip_not_open
        self._include = [k.strip().lower() for k in keywords_include or [] if k.strip()]
        self._exclude = [k.strip().lower() for k in keywords_exclude or [] if k.strip()]

    def match(self, card: B2BCard, *, now: datetime | None = None) -> FilterDecision:
        at = now or datetime.now(UTC)
        reasons: list[str] = []
        status = (card.status or "").lower()
        if self._skip_not_actual and status in TERMINAL_STATUSES:
            reasons.append(f"terminal status {status}")
        if self._skip_not_open and card.is_open is False:
            reasons.append("closed participation")
        if card.date_end is not None:
            end = card.date_end if card.date_end.tzinfo else card.date_end.replace(tzinfo=UTC)
            remaining = (end - at).total_seconds() / 3600.0
            if remaining < 0:
                reasons.append("deadline passed")
            elif self._min_remaining_hours and remaining < self._min_remaining_hours:
                reasons.append(f"remaining {remaining:.1f}h < {self._min_remaining_hours:g}h")

        haystack = "\n".join(
            [
                card.name or "",
                card.description or "",
                *card.position_names,
                *(lot.name for lot in card.lots),
            ]
        ).lower()
        if self._include and not any(k in haystack for k in self._include):
            reasons.append("no required keyword")
        if self._exclude and any(k in haystack for k in self._exclude):
            reasons.append("excluded keyword")

        category = ""
        okpd = card.okpd_codes
        for name in self._selected:
            rule = CATEGORY_RULES[name]
            if any(code.startswith(tuple(rule["okpd"])) for code in okpd):
                category = name
                break
            if any(keyword.lower() in haystack for keyword in rule["keywords"]):
                category = name
                break
        if not category:
            reasons.append(f"no category match (selected: {', '.join(self._selected)})")
        return FilterDecision(matched=not reasons, category=category, reasons=reasons)
