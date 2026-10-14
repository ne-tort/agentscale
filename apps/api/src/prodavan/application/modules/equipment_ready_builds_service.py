"""WAVE12 — пайплайн каталога «Готовые сборки» (v2: карточки + характеристики).

Модель v2 (docs/WAVE12-READY-BUILDS-REWORK.md):

- ``equipment_types`` — шаблоны: ``fields_json`` описывает характеристики типа
  (``kind``: text|enum|groups, ``required``, ``compat_domain``, ``values`` /
  ``groups``). Значения характеристик — заранее заданные группы, не строки;
- ``part_cards`` — «Карточки комплектующих»: ключи (партномер/алиасы/хэш) +
  ``type_id`` + ``attrs`` (значения из групп типа). Цены-лидеры резолвятся
  батч-запросом в OpenSearch (как и раньше — каталог хранит ключи, не офферы);
- ``ready_builds`` + ``ready_build_slots`` — сборка = набор слотов
  ``type_id`` + ``constraints`` (поле → значение группы) + ``qty`` + опционально
  закреплённая ``card_id``. Групп совместимости и бюджетных классов больше нет.

Совместимость — автоматическая матрица из ``compat_domain``: поля разных типов
с одним доменом (``socket`` у CPU/материнки/кулера, ``ram_type`` у материнки и
ОЗУ, ``form_factor`` у материнки и корпуса) обязаны совпадать. Конфликты не
блокируют сборку (грубой совместимости достаточно), а пишутся в
``compat_issues`` — видно и в UI, и агенту.

Цена: для каждого слота кандидаты = карточки типа, чьи ``attrs`` покрывают
``constraints`` (+ фильтр по зафиксированным значениям доменов); у каждой
карточки — лидер цены по её ключам. Без целевого коридора выбирается дешевейший
кандидат. С ``target_price_min``/``target_price_max`` перебирается пара десятков
умных комбинаций (all-cheapest, all-priciest, покомпонентные апгрейды, жадные
проходы к коридору) — детерминированно, без полной комбинаторики. ``price_min``
/``price_max`` на сборке — коридор возможных конфигураций (дешевейшая/дорожайшая
комбинация из текущих кандидатов).

Все таблицы каталога ``chats: all`` → строки имеют ``session_id = NULL`` и
читаются обычным ``io.list``. Записи идут через ``ModuleRowIO``
(``run_actions=False``) — пайплайн не самотриггерится. Пишутся только
изменённые поля: иначе каждый прогон переписывает все строки и проект ложно
помечается «требует обновления».
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from prodavan.application.modules.equipment_offers_service import (
    CATALOGS_TABLE,
    SELLERS_TABLE,
    ModuleRowIO,
    _num_or,
    _SellerRegistry,
    assign_docs_to_keys,
    best_price_from_docs,
    search_catalog_docs,
)

logger = logging.getLogger(__name__)

CARDS_TABLE = "part_cards"
BUILDS_TABLE = "ready_builds"
SLOTS_TABLE = "ready_build_slots"
TYPES_TABLE = "equipment_types"

# Идентификаторы типов, чья численность показываем отдельно (плашки, диски).
# Сидятся со стабильными id в product_module_seeds.
RAM_TYPE_ID = "etype_ram"
STORAGE_TYPE_ID = "etype_storage"

# WAVE13: сплит-комбинаторика для ОЗУ и накопителей. В слоте требование задаёт
# СУММУ (ram_total / storage_total), а резолвер решает, как её набрать:
# «32 ГБ ОЗУ» = 1×32 или 2×16 или 4×8 — выбирается дешевейший вариант.
# Ключ карточки остаётся по-модульный (module_capacity / capacity), потому что
# карточка описывает ОДИН модуль; тотал — только в слоте.
RAM_TOTAL_KEY = "ram_total"
STORAGE_TOTAL_KEY = "storage_total"
# поле модуля/диска в attrs карточки для каждого «тотал»-типа
_TOTAL_CAPACITY_FIELD = {
    RAM_TYPE_ID: ("ram_total", "module_capacity"),
    STORAGE_TYPE_ID: ("storage_total", "capacity"),
}
_SPLIT_MULTIPLIERS = (1, 2, 4)

# Сколько кандидатов слота участвует в комбинаторике коридора цен: дешевейший,
# квартили, дорожайший. Полного перебора нет по дизайну.
COMBO_SPREAD = 5
# Жёсткий потолок комбинаций — паттерны дают ~2+4×слотов+2, но не доверяем.
COMBO_LIMIT = 40


def _qty_of(raw: Any) -> float:
    """Количество слота: целое ≥ 1; мусор и 0 дают 1."""
    value = _num_or(raw, None)
    if value is None or value < 1:
        return 1.0
    return float(int(value))


def _norm(value: Any) -> str:
    return str(value or "").strip().casefold()


# ------------------------------------------------------------------ шаблоны


def parse_type_schemas(type_rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """``{type_id: {"name", "sort_order", "fields": {key: field_def}}}``.

    field_def: ``{key, label, kind, required, compat_domain, choices}`` —
    ``choices`` = ``{value: label}`` для kind enum/groups, пусто для text.
    Старый формат fields_json (только key/label) читается как kind=text.
    """
    out: dict[str, dict[str, Any]] = {}
    for row in type_rows:
        if not isinstance(row, dict):
            continue
        type_id = str(row.get("row_id") or "")
        body = row.get("body") if isinstance(row.get("body"), dict) else {}
        fields: dict[str, dict[str, Any]] = {}
        raw_fields = body.get("fields_json")
        for f in raw_fields if isinstance(raw_fields, list) else []:
            if not isinstance(f, dict):
                continue
            key = str(f.get("key") or "").strip()
            if not key:
                continue
            label = f.get("label")
            if isinstance(label, dict):
                label = str(label.get("ru") or label.get("en") or key)
            kind = str(f.get("kind") or "text").strip() or "text"
            choices: dict[str, str] = {}
            if kind == "enum":
                for v in f.get("values") if isinstance(f.get("values"), list) else []:
                    sv = str(v).strip()
                    if sv:
                        choices[sv] = sv
            elif kind == "groups":
                for g in f.get("groups") if isinstance(f.get("groups"), list) else []:
                    if isinstance(g, dict):
                        sv = str(g.get("value") or "").strip()
                        sl = str(g.get("label") or "").strip() or sv
                    else:
                        sv = str(g).strip()
                        sl = sv
                    if sv:
                        choices[sv] = sl
            fields[key] = {
                "key": key,
                "label": str(label or key),
                "kind": kind,
                "required": bool(f.get("required")),
                "compat_domain": str(f.get("compat_domain") or "").strip(),
                "choices": choices,
            }
        out[type_id] = {
            "name": str(body.get("name") or "").strip(),
            "sort_order": _num_or(body.get("sort_order"), 1000.0) or 1000.0,
            "fields": fields,
        }
    return out


def value_label(field_def: dict[str, Any], value: Any) -> str:
    """Человекочитаемая подпись значения: label группы/enum или само значение."""
    raw = str(value or "").strip()
    if not raw:
        return ""
    choices = field_def.get("choices") if isinstance(field_def.get("choices"), dict) else {}
    return str(choices.get(raw) or raw)


def attrs_label(fields: dict[str, dict[str, Any]], attrs: Any) -> str:
    """«SSD · 512 ГБ» — подпись attrs/constraints по шаблону типа (в порядке полей)."""
    if not isinstance(attrs, dict) or not attrs:
        return ""
    parts: list[str] = []
    for key, fdef in fields.items():
        if key not in attrs:
            continue
        label = value_label(fdef, attrs.get(key))
        if label:
            parts.append(label)
    # ключи вне шаблона не теряем
    for key in sorted(k for k in attrs if k not in fields):
        raw = str(attrs.get(key) or "").strip()
        if raw:
            parts.append(raw)
    return " · ".join(parts)


def card_matches_constraints(
    card_attrs: Any, constraints: Any, fields: dict[str, dict[str, Any]]
) -> bool:
    """Карточка покрывает требования слота: каждое constraint-поле совпадает."""
    if not isinstance(constraints, dict) or not constraints:
        return True
    attrs = card_attrs if isinstance(card_attrs, dict) else {}
    for key, want in constraints.items():
        if _norm(want) == "":
            continue
        if _norm(attrs.get(key)) != _norm(want):
            return False
    return True


def validate_card_attrs(
    fields: dict[str, dict[str, Any]], attrs: Any
) -> list[str]:
    """Ошибки заполнения карточки: required-поля и значения вне групп."""
    errors: list[str] = []
    values = attrs if isinstance(attrs, dict) else {}
    for key, fdef in fields.items():
        raw = values.get(key)
        empty = raw is None or str(raw).strip() == ""
        if fdef.get("required") and empty:
            errors.append(f"обязательное поле «{fdef.get('label') or key}» не заполнено")
            continue
        if empty:
            continue
        choices = fdef.get("choices") or {}
        if choices and str(raw).strip() not in choices:
            allowed = ", ".join(list(choices)[:12])
            errors.append(
                f"«{fdef.get('label') or key}»: значение «{raw}» вне групп ({allowed}…)"
            )
    return errors


# ------------------------------------------------------------------ пайплайн


class ReadyBuildsPipelineService:
    """Резолв цен карточек + подбор компонентов сборок по constraints."""

    def __init__(self, *, session: Any) -> None:
        self._session = session

    async def run(self, io: ModuleRowIO, *, resolve: bool = True) -> dict[str, Any]:
        stats: dict[str, Any] = {
            "kind": "equipment.ready_builds",
            "resolve": bool(resolve),
            "cards": 0,
            "builds": 0,
            "slots": 0,
            "cards_updated": 0,
            "slots_updated": 0,
            "builds_updated": 0,
            "unresolved_slots": 0,
            "combos_tried": 0,
        }

        cards = [r for r in await io.list(CARDS_TABLE) if isinstance(r, dict)]
        builds = [r for r in await io.list(BUILDS_TABLE) if isinstance(r, dict)]
        slots = [r for r in await io.list(SLOTS_TABLE) if isinstance(r, dict)]
        type_rows = [r for r in await io.list(TYPES_TABLE) if isinstance(r, dict)]
        stats["cards"] = len(cards)
        stats["builds"] = len(builds)
        stats["slots"] = len(slots)
        if not cards and not slots and not builds:
            return stats

        schemas = parse_type_schemas(type_rows)
        sellers = [r for r in await io.list(SELLERS_TABLE) if isinstance(r, dict)]
        registry = _SellerRegistry(sellers)
        now = datetime.now(UTC).isoformat(timespec="seconds")

        # ------------------------------------------------ резолв цен карточек
        best_by_card: dict[str, dict[str, Any]] = {}
        if resolve:
            best_by_card = await self._resolve_card_prices(
                io, cards=cards, registry=registry
            )

        cards_by_id: dict[str, dict[str, Any]] = {}
        for card in cards:
            cid = str(card.get("row_id") or "")
            body = dict(card.get("body") or {})
            best = best_by_card.get(cid)
            type_id = str(body.get("type_id") or "")
            schema = schemas.get(type_id) or {}
            updates: dict[str, Any] = {}
            want_count = int((best or {}).get("offers_count") or 0)
            if int(_num_or(body.get("offers_count"), 0) or 0) != want_count:
                updates["offers_count"] = want_count
            want_price = (best or {}).get("price")
            if _num_or(body.get("best_price"), None) != _num_or(want_price, None):
                updates["best_price"] = want_price
            for field, key in (("best_title", "title"), ("best_seller", "seller")):
                want = str((best or {}).get(key) or "")
                if str(body.get(field) or "") != want:
                    updates[field] = want
            want_stock = bool((best or {}).get("in_stock"))
            if bool(body.get("in_stock")) is not want_stock:
                updates["in_stock"] = want_stock
            want_type_name = str(schema.get("name") or "")
            if want_type_name and str(body.get("type_name") or "") != want_type_name:
                updates["type_name"] = want_type_name
            want_attrs_label = attrs_label(schema.get("fields") or {}, body.get("attrs"))
            if str(body.get("attrs_label") or "") != want_attrs_label:
                updates["attrs_label"] = want_attrs_label
            if updates:
                updates["synced_at"] = now
                body.update(updates)
                await io.update(CARDS_TABLE, cid, body)
                card["body"] = body
                stats["cards_updated"] += 1
            cards_by_id[cid] = card

        # ------------------------------------------------ слоты по сборкам
        slots_by_build: dict[str, list[dict[str, Any]]] = {}
        for slot in slots:
            bid = str((slot.get("body") or {}).get("build_id") or "")
            slots_by_build.setdefault(bid, []).append(slot)

        for build in builds:
            bid = str(build.get("row_id") or "")
            build_slots = slots_by_build.get(bid) or []
            # порядок слотов — по sort_order типа: стабильные комбинации и
            # одинаковый spec_summary между прогонами
            build_slots.sort(
                key=lambda s: (
                    (schemas.get(str((s.get("body") or {}).get("type_id") or "")) or {}).get(
                        "sort_order", 1000.0
                    ),
                    str(s.get("row_id") or ""),
                )
            )
            resolution = self._resolve_build(
                build_body=dict(build.get("body") or {}),
                build_slots=build_slots,
                cards_by_id=cards_by_id,
                schemas=schemas,
            )
            stats["unresolved_slots"] += resolution["unresolved"]
            stats["combos_tried"] += resolution["combos_tried"]

            # запись слотов
            for slot in build_slots:
                sid = str(slot.get("row_id") or "")
                body = dict(slot.get("body") or {})
                res = resolution["slots"].get(sid) or {}
                updates = {}
                chosen_card_id = str(res.get("card_id") or "")
                if str(body.get("resolved_card_id") or "") != chosen_card_id:
                    updates["resolved_card_id"] = chosen_card_id
                for field, key in (
                    ("resolved_title", "title"),
                    ("resolved_part_number", "part_number"),
                    ("resolved_seller", "seller"),
                ):
                    want = str(res.get(key) or "")
                    if str(body.get(field) or "") != want:
                        updates[field] = want
                want_price = res.get("price")
                if _num_or(body.get("resolved_price"), None) != _num_or(want_price, None):
                    updates["resolved_price"] = want_price
                want_line = res.get("line_total")
                if _num_or(body.get("line_total"), None) != want_line:
                    updates["line_total"] = want_line
                want_stock = bool(res.get("in_stock"))
                if bool(body.get("in_stock")) is not want_stock:
                    updates["in_stock"] = want_stock
                want_alts = int(res.get("alternatives") or 0)
                if int(_num_or(body.get("alternatives_count"), 0) or 0) != want_alts:
                    updates["alternatives_count"] = want_alts
                type_id = str(body.get("type_id") or "")
                schema = schemas.get(type_id) or {}
                want_type_name = str(schema.get("name") or "")
                if want_type_name and str(body.get("type_name") or "") != want_type_name:
                    updates["type_name"] = want_type_name
                want_constraints_label = attrs_label(
                    schema.get("fields") or {}, body.get("constraints")
                )
                if str(body.get("constraints_label") or "") != want_constraints_label:
                    updates["constraints_label"] = want_constraints_label
                # сплит (2×16 вместо 1×32): фактическое количество пишется на
                # слот — зеркало отдаст его в чат как slot_qty
                want_qty = _qty_of(res.get("qty"))
                if _qty_of(body.get("qty")) != want_qty:
                    updates["qty"] = want_qty
                if updates:
                    updates["synced_at"] = now
                    body.update(updates)
                    await io.update(SLOTS_TABLE, sid, body)
                    slot["body"] = body
                    stats["slots_updated"] += 1

            # ------------------------------------------------ итоги сборки
            body = dict(build.get("body") or {})
            updates = {}
            slots_count = len(build_slots)
            qty_total = 0.0
            ram_sticks = 0.0
            storage_drives = 0.0
            for slot in build_slots:
                sbody = slot.get("body") or {}
                slot_type = str(sbody.get("type_id") or "")
                # количество — из резолюции (сплит мог переписать qty слота)
                res = resolution["slots"].get(str(slot.get("row_id") or "")) or {}
                qty = _qty_of(res.get("qty") or sbody.get("qty"))
                qty_total += qty
                if slot_type == RAM_TYPE_ID:
                    ram_sticks += qty
                elif slot_type == STORAGE_TYPE_ID:
                    storage_drives += qty
            aggregates = {
                "slots_count": slots_count,
                "qty_total": qty_total,
                "ram_sticks": ram_sticks,
                "storage_drives": storage_drives,
                "price_total": resolution["price_total"],
                "price_min": resolution["price_min"],
                "price_max": resolution["price_max"],
                "unresolved_count": resolution["unresolved"],
                "on_order": resolution["on_order"],
                "compat_ok": resolution["compat_ok"],
                "compat_issues": resolution["compat_issues"],
                "price_note": resolution["price_note"],
                "spec_summary": resolution["spec_summary"],
            }
            for field, want in aggregates.items():
                if isinstance(want, bool):
                    # compat_ok по умолчанию True (отсутствие ключа = True):
                    # False обязан быть записан явно; остальные флаги наоборот
                    default = True if field == "compat_ok" else False
                    if bool(body.get(field, default)) is not want:
                        updates[field] = want
                elif isinstance(want, float) or want is None:
                    if _num_or(body.get(field), None) != _num_or(want, None):
                        updates[field] = want
                else:
                    if body.get(field) != want:
                        updates[field] = want
            if updates:
                updates["synced_at"] = now
                body.update(updates)
                await io.update(BUILDS_TABLE, bid, body)
                build["body"] = body
                stats["builds_updated"] += 1

        return stats

    # ----------------------------------------------------- резолв цен карточек

    async def _resolve_card_prices(
        self,
        io: ModuleRowIO,
        *,
        cards: list[dict[str, Any]],
        registry: _SellerRegistry,
    ) -> dict[str, dict[str, Any]]:
        """Один батч-запрос в OS на все ключи карточек → лидер цены на карточку."""
        catalogs = [r for r in await io.list(CATALOGS_TABLE) if isinstance(r, dict)]
        ready_ids = [
            str(c.get("row_id"))
            for c in catalogs
            if str((c.get("body") or {}).get("status") or "").strip().lower() == "ready"
        ]
        if not ready_ids:
            return {}

        bodies: dict[str, dict[str, Any]] = {}
        for card in cards:
            cid = str(card.get("row_id") or "")
            if cid:
                bodies[cid] = card.get("body") or {}
        docs = await search_catalog_docs(
            self._session, ready_ids=ready_ids, bodies=list(bodies.values())
        )
        if not docs:
            return {}
        per_card = assign_docs_to_keys(docs, bodies, registry=registry)
        out: dict[str, dict[str, Any]] = {}
        for cid, card_docs in per_card.items():
            best = await best_price_from_docs(card_docs, registry=registry)
            if best is not None:
                out[cid] = best
        return out

    # ------------------------------------------------------- резолв одной сборки

    def _resolve_build(
        self,
        *,
        build_body: dict[str, Any],
        build_slots: list[dict[str, Any]],
        cards_by_id: dict[str, dict[str, Any]],
        schemas: dict[str, dict[str, Any]],
    ) -> dict[str, Any]:
        """Подбор компонентов сборки: constraints → кандидаты → комбинация.

        Шаги: фиксация доменов совместимости (constraints + закреплённые
        карточки) → кандидаты слота (constraints + домен) → выбор комбинации
        (коридор цен или дешевейшая) → проверка совместимости выбранного.
        """
        issues: list[str] = []

        # ---- фиксация значений доменов (constraints + закреплённые карточки)
        domain_fixed: dict[str, str] = {}
        for slot in build_slots:
            sbody = slot.get("body") or {}
            type_id = str(sbody.get("type_id") or "")
            fields = (schemas.get(type_id) or {}).get("fields") or {}
            constraints = (
                sbody.get("constraints") if isinstance(sbody.get("constraints"), dict) else {}
            )
            pinned = cards_by_id.get(str(sbody.get("card_id") or ""))
            pinned_attrs = (pinned.get("body") or {}).get("attrs") if pinned else None
            for key, fdef in fields.items():
                domain = str(fdef.get("compat_domain") or "")
                if not domain:
                    continue
                value = constraints.get(key)
                if _norm(value) == "" and isinstance(pinned_attrs, dict):
                    value = pinned_attrs.get(key)
                if _norm(value) == "":
                    continue
                norm_value = _norm(value)
                if domain in domain_fixed and domain_fixed[domain] != norm_value:
                    issues.append(
                        f"домен «{domain}»: конфликт {domain_fixed[domain]} ≠ {norm_value}"
                    )
                    continue
                domain_fixed.setdefault(domain, norm_value)

        # ---- кандидаты слотов
        slot_states: list[dict[str, Any]] = []
        for slot in build_slots:
            sid = str(slot.get("row_id") or "")
            sbody = slot.get("body") or {}
            type_id = str(sbody.get("type_id") or "")
            schema = schemas.get(type_id) or {}
            fields = schema.get("fields") or {}
            constraints = (
                sbody.get("constraints") if isinstance(sbody.get("constraints"), dict) else {}
            )
            qty = _qty_of(sbody.get("qty"))
            pinned_id = str(sbody.get("card_id") or "").strip()
            # тотал-поля (ram_total/storage_total) не существуют в карточках —
            # в прямое сравнение они не идут, их обрабатывает сплит-логика
            total_key = _TOTAL_CAPACITY_FIELD.get(type_id, (None, None))[0]
            match_constraints = {
                k: v for k, v in constraints.items() if k != total_key
            }

            candidates: list[dict[str, Any]] = []
            if pinned_id:
                pinned = cards_by_id.get(pinned_id)
                if pinned is None:
                    issues.append(
                        f"{schema.get('name') or type_id}: закреплённая карточка удалена"
                    )
                else:
                    candidates = [pinned]
                    if not card_matches_constraints(
                        (pinned.get("body") or {}).get("attrs"), match_constraints, fields
                    ):
                        issues.append(
                            f"{schema.get('name') or type_id}: закреплённая карточка "
                            f"не отвечает требованиям слота"
                        )
            else:
                for card in cards_by_id.values():
                    cbody = card.get("body") or {}
                    if str(cbody.get("type_id") or "") != type_id:
                        continue
                    if not card_matches_constraints(cbody.get("attrs"), match_constraints, fields):
                        continue
                    candidates.append(card)
                # доменный фильтр: если домен зафиксирован, поле типа входит в
                # домен и constraint его не пинает — предпочитаем согласованных
                for key, fdef in fields.items():
                    domain = str(fdef.get("compat_domain") or "")
                    fixed = domain_fixed.get(domain)
                    if not domain or not fixed or _norm(constraints.get(key)):
                        continue
                    agreed = [
                        c
                        for c in candidates
                        if _norm(
                            (c.get("body") or {}).get("attrs", {}).get(key)
                            if isinstance((c.get("body") or {}).get("attrs"), dict)
                            else None
                        )
                        == fixed
                    ]
                    if agreed:
                        candidates = agreed
                    elif candidates:
                        issues.append(
                            f"{schema.get('name') or type_id}: нет карточек с "
                            f"{key}={fixed} — совместимость по «{domain}» ослаблена"
                        )

            # ценовые кандидаты: (card_id, сумма_слота, наличие, qty_факт).
            # Сумма слота уже включает количество — по ней и сравниваем.
            priced: list[tuple[str, float, bool, float]] = []
            total_key, per_field = _TOTAL_CAPACITY_FIELD.get(type_id, (None, None))
            total_raw = constraints.get(total_key) if total_key else None
            total_gb = _num_or(total_raw, None) if total_raw is not None else None
            if total_key and total_gb and total_gb > 0 and not pinned_id:
                # сплит-комбинаторика: «32 = 1×32 | 2×16 | 4×8» — дешевле побеждает.
                for k in _SPLIT_MULTIPLIERS:
                    if total_gb % k != 0:
                        continue
                    per = total_gb // k
                    per_str = str(int(per))
                    best = self._cheapest_card(
                        candidates=(),  # по тоталу не фильтруем — берём с нуля
                        cards_by_id=cards_by_id,
                        type_id=type_id,
                        match_constraints=match_constraints,
                        per_field=per_field,
                        per_value=per_str,
                    )
                    if best is not None:
                        priced.append(
                            (best[0], best[1] * k, best[2], float(k))
                        )
            if not priced:
                for card in candidates:
                    cid = str(card.get("row_id") or "")
                    cbody = card.get("body") or {}
                    price = _num_or(cbody.get("best_price"), None)
                    if price is None or int(_num_or(cbody.get("offers_count"), 0) or 0) <= 0:
                        continue
                    priced.append((cid, float(price) * qty, bool(cbody.get("in_stock")), qty))
            priced.sort(key=lambda t: (t[1], t[0]))

            slot_states.append(
                {
                    "slot_id": sid,
                    "type_id": type_id,
                    "type_name": str(schema.get("name") or type_id),
                    "qty": qty,
                    "constraints": constraints,
                    "fields": fields,
                    "candidates": candidates,
                    "priced": priced,
                    "pinned_id": pinned_id,
                }
            )

        # ПК всегда включает корпус и охлаждение процессора: их отсутствие —
        # не ошибка резолва, но заметное замечание (видно в карточке сборки).
        if str(build_body.get("build_kind") or "pc") == "pc":
            present = {s["type_id"] for s in slot_states}
            if "etype_case" not in present:
                issues.append("нет слота корпуса (etype_case)")
            if "etype_cooling" not in present:
                issues.append("нет слота охлаждения (etype_cooling)")

        # ---- выбор комбинации (коридор цен с тела сборки)
        target_min = _num_or(build_body.get("target_price_min"), None)
        target_max = _num_or(build_body.get("target_price_max"), None)
        if target_min is not None and target_min <= 0:
            target_min = None
        if target_max is not None and target_max <= 0:
            target_max = None
        chosen, price_note, combos_tried, totals = self._choose_combo(
            slot_states,
            target_min=target_min,
            target_max=target_max,
        )

        # ---- результат по слотам
        out_slots: dict[str, dict[str, Any]] = {}
        unresolved = 0
        on_order = False
        for state in slot_states:
            sid = state["slot_id"]
            chosen_id = chosen.get(sid, "")
            # выбранная запись несёт сумму слота и фактическое количество
            # (для сплита 2×16 вместо 1×32: qty переписываем на слоте)
            chosen_entry = next(
                (e for e in state["priced"] if e[0] == chosen_id), None
            )
            card = cards_by_id.get(chosen_id) if chosen_id else None
            cbody = (card or {}).get("body") or {}
            unit_price = _num_or(cbody.get("best_price"), None) if card else None
            if card is None or unit_price is None or chosen_entry is None:
                unresolved += 1
                if not state["priced"] and not state["pinned_id"]:
                    issues.append(
                        f"{state['type_name']}: нет карточек по требованиям слота"
                    )
            in_stock = bool(cbody.get("in_stock")) if card else False
            if card is not None and not in_stock:
                on_order = True
            line_total = chosen_entry[1] if chosen_entry else None
            out_slots[sid] = {
                "card_id": chosen_id,
                "title": str(cbody.get("best_title") or ""),
                "part_number": str(cbody.get("part_number") or ""),
                "seller": str(cbody.get("best_seller") or ""),
                "price": unit_price,
                "in_stock": in_stock,
                "line_total": line_total,
                "alternatives": max(0, len(state["priced"]) - 1),
                # фактическое количество (для сплита) — зеркало отдаст в чат
                "qty": chosen_entry[3] if chosen_entry else state["qty"],
            }

        # ---- совместимость выбранной комбинации по доменам
        domain_seen: dict[str, dict[str, list[str]]] = {}
        for state in slot_states:
            chosen_id = chosen.get(state["slot_id"], "")
            card = cards_by_id.get(chosen_id) if chosen_id else None
            card_attrs = (card.get("body") or {}).get("attrs") if card else None
            for key, fdef in (state["fields"] or {}).items():
                domain = str(fdef.get("compat_domain") or "")
                if not domain:
                    continue
                value = state["constraints"].get(key)
                if _norm(value) == "" and isinstance(card_attrs, dict):
                    value = card_attrs.get(key)
                if _norm(value) == "":
                    continue
                domain_seen.setdefault(domain, {}).setdefault(_norm(value), []).append(
                    state["type_name"]
                )
        compat_ok = True
        for domain, values in domain_seen.items():
            if len(values) > 1:
                compat_ok = False
                detail = ", ".join(
                    f"{v} ({', '.join(sorted(set(owners)))})" for v, owners in values.items()
                )
                issues.append(f"домен «{domain}» не сходится: {detail}")
        if issues:
            compat_ok = False

        # ---- spec_summary: «Процессор: LGA1700 · 6 ядер; ОЗУ: DDR4 · 16 ГБ ×2»
        spec_parts: list[str] = []
        for state in slot_states:
            label = attrs_label(state["fields"] or {}, state["constraints"])
            part = f"{state['type_name']}: {label}" if label else state["type_name"]
            # фактическое количество (после сплита) — из результата, не из слота
            actual_qty = (out_slots.get(state["slot_id"]) or {}).get("qty") or state["qty"]
            if actual_qty > 1:
                part = f"{part} ×{int(actual_qty)}"
            spec_parts.append(part)

        return {
            "slots": out_slots,
            "unresolved": unresolved,
            "on_order": on_order,
            "price_total": totals.get("price_total"),
            "price_min": totals.get("price_min"),
            "price_max": totals.get("price_max"),
            "compat_ok": compat_ok,
            "compat_issues": " · ".join(dict.fromkeys(issues)),
            "price_note": price_note,
            "spec_summary": " · ".join(spec_parts),
            "combos_tried": combos_tried,
        }

    # ------------------------------------------------------------ комбинаторика

    @staticmethod
    def _cheapest_card(
        *,
        candidates: Any,
        cards_by_id: dict[str, dict[str, Any]],
        type_id: str,
        match_constraints: dict[str, Any],
        per_field: str,
        per_value: str,
    ) -> tuple[str, float, bool] | None:
        """Дешевейшая карточка типа с attrs[per_field] == per_value и покрытием
        остальных требований (без тотал-ключа — он и заменён на per_value)."""
        best: tuple[str, float, bool] | None = None
        for card in cards_by_id.values():
            cbody = card.get("body") or {}
            if str(cbody.get("type_id") or "") != type_id:
                continue
            attrs = cbody.get("attrs") if isinstance(cbody.get("attrs"), dict) else {}
            if _norm(attrs.get(per_field)) != _norm(per_value):
                continue
            if not card_matches_constraints(attrs, match_constraints, {}):
                continue
            price = _num_or(cbody.get("best_price"), None)
            if price is None or int(_num_or(cbody.get("offers_count"), 0) or 0) <= 0:
                continue
            cid = str(card.get("row_id") or "")
            cand = (cid, float(price), bool(cbody.get("in_stock")))
            if best is None or (cand[1], cand[0]) < (best[1], best[0]):
                best = cand
        return best

    @staticmethod
    def _spread(priced: list[tuple[str, float, bool, float]]) -> list[int]:
        """Индексы кандидатов для комбинаторики: дешевейший, квартили, дорожайший."""
        n = len(priced)
        if n <= COMBO_SPREAD:
            return list(range(n))
        marks = {0, n // 4, n // 2, (3 * n) // 4, n - 1}
        return sorted(marks)

    def _choose_combo(
        self,
        slot_states: list[dict[str, Any]],
        *,
        target_min: float | None,
        target_max: float | None,
    ) -> tuple[dict[str, str], str, int, dict[str, Any]]:
        """Выбор комбинации кандидатов по слотам.

        Возвращает (chosen {slot_id: card_id}, note, combos_tried, totals).
        Детерминированно: кандидаты отсортированы (цена, card_id), паттерны
        комбинаций фиксированы.
        """
        chosen: dict[str, str] = {}
        totals: dict[str, Any] = {}
        active = [s for s in slot_states if s["priced"]]
        # слоты без ценовых кандидатов — неразрешённые, выбора нет
        if not active:
            return chosen, "", 0, totals

        def total_of(combo: tuple[int, ...]) -> float:
            # сумма слота уже включает количество (сплит: 2×16 вместо 32)
            total = 0.0
            for state, idx in zip(active, combo):
                total += state["priced"][idx][1]
            return round(total, 2)

        spreads = [self._spread(s["priced"]) for s in active]
        cheapest = tuple(sp[0] for sp in spreads)
        priciest = tuple(sp[-1] for sp in spreads)

        combos: set[tuple[int, ...]] = {cheapest, priciest}
        # покомпонентные апгрейды от дешевейшей базы
        for i, sp in enumerate(spreads):
            for idx in sp[1:]:
                combo = list(cheapest)
                combo[i] = idx
                combos.add(tuple(combo))

        if target_max is not None:
            # жадный проход к потолку: каждый шаг — самый выгодный апгрейд,
            # не выходящий за target_max
            combo = list(cheapest)
            while True:
                current = total_of(tuple(combo))
                best_move: tuple[int, int, float] | None = None
                for i, sp in enumerate(spreads):
                    higher = [idx for idx in sp if idx > combo[i]]
                    if not higher:
                        continue
                    nxt = higher[0]
                    # дельта по цене слота (количество уже входит в цену записи)
                    delta = active[i]["priced"][nxt][1] - active[i]["priced"][combo[i]][1]
                    if delta <= 0:
                        continue
                    if current + delta > target_max:
                        continue
                    if best_move is None or delta > best_move[2]:
                        best_move = (i, nxt, delta)
                if best_move is None:
                    break
                combo[best_move[0]] = best_move[1]
                combos.add(tuple(combo))
                if len(combos) >= COMBO_LIMIT:
                    break

        if target_min is not None and total_of(cheapest) < target_min:
            # жадный проход к минимуму: самые дешёвые апгрейды, пока не доберём
            combo = list(cheapest)
            while total_of(tuple(combo)) < target_min and len(combos) < COMBO_LIMIT:
                best_move = None
                for i, sp in enumerate(spreads):
                    higher = [idx for idx in sp if idx > combo[i]]
                    if not higher:
                        continue
                    nxt = higher[0]
                    delta = active[i]["priced"][nxt][1] - active[i]["priced"][combo[i]][1]
                    if delta <= 0:
                        continue
                    if best_move is None or delta < best_move[2]:
                        best_move = (i, nxt, delta)
                if best_move is None:
                    break
                combo[best_move[0]] = best_move[1]
                combos.add(tuple(combo))

        ranked = sorted(combos, key=lambda c: (total_of(c), c))[:COMBO_LIMIT]
        combos_tried = len(ranked)

        def _combo_total(c: tuple[int, ...]) -> float:
            return total_of(c)

        if target_min is None and target_max is None:
            picked = cheapest
            note = ""
        else:
            in_corridor = [
                c
                for c in ranked
                if (target_min is None or _combo_total(c) >= target_min)
                and (target_max is None or _combo_total(c) <= target_max)
            ]
            if in_corridor:
                # с потолком — максимально наполненная конфигурация в коридоре;
                # только с минимумом — дешевейшая, которая его достигает
                picked = (
                    max(in_corridor, key=lambda c: (_combo_total(c), c))
                    if target_max is not None
                    else min(in_corridor, key=lambda c: (_combo_total(c), c))
                )
                note = ""
            else:
                totals_list = [_combo_total(c) for c in ranked]
                if target_min is not None and max(totals_list) < target_min:
                    picked = max(ranked, key=lambda c: (_combo_total(c), c))
                    note = "целевой минимум недостижим — выбрана максимально близкая конфигурация"
                elif target_max is not None and min(totals_list) > target_max:
                    picked = cheapest
                    note = "целевой максимум недостижим — выбрана минимальная конфигурация"
                else:
                    picked = cheapest
                    note = "целевой коридор недостижим — выбрана минимальная конфигурация"

        for state, idx in zip(active, picked):
            chosen[state["slot_id"]] = state["priced"][idx][0]

        # коридор возможных конфигураций (по полным спискам кандидатов, не spread)
        totals["price_min"] = round(
            sum(s["priced"][0][1] for s in active), 2
        )
        totals["price_max"] = round(
            sum(s["priced"][-1][1] for s in active), 2
        )
        totals["price_total"] = total_of(picked)
        return chosen, note, combos_tried, totals
