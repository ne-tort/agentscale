"""WAVE11 — пайплайн каталога «Готовые сборки».

Каталог хранит **ключи** (партномер / алиасы / хэш позиции), а не товары
конкретных поставщиков. Этот сервис периодически резолвит ключи в лучшие цены
батч-запросом в OpenSearch и пересчитывает состав сборок, поэтому каталог
обновляется сам — и по ценам, и по комплектующим.

Модель (docs/WAVE11-READY-BUILDS.md):

- ``build_groups`` — домен совместимости (сокет / тип ОЗУ / форм-фактор)
  + бюджетный класс;
- ``build_group_items`` — пул оборудования группы: одна строка = одна
  альтернатива компонента, с ``class_key`` (``ram_16``, ``ssd_256`` …). Класс
  не даёт сравнивать несравнимое;
- ``ready_builds`` — вариант конфигурации внутри группы;
- ``ready_build_slots`` — слот: ``type_id`` + ``qty`` + ``mode``
  (``dynamic`` → дешевейший доступный компонент класса, может дрейфовать;
  ``fixed`` → закреплён, цена меняется, состав нет).

Инвариант ранжирования: «дешевлейшая сборка» считается **строго внутри**
``(group_id, class_signature)``. Разные сигнатуры — разные витрины, они не
конкурируют, поэтому «16 ГБ дешевле 32 ГБ» никогда не делает 16 ГБ победителем.

Все таблицы каталога ``chats: all`` → строки имеют ``session_id = NULL`` и
читаются обычным ``io.list`` (``list_project_wide`` фильтрует
``session_id IN (...)`` и NULL-строки не видит).

Записи идут через ``ModuleRowIO`` (``run_actions=False``) — пайплайн не
самотриггерится. Пишутся только изменённые поля: иначе каждый прогон
переписывает все строки и проект ложно помечается «требует обновления».
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

GROUPS_TABLE = "build_groups"
POOL_TABLE = "build_group_items"
BUILDS_TABLE = "ready_builds"
SLOTS_TABLE = "ready_build_slots"

MODE_DYNAMIC = "dynamic"
MODE_FIXED = "fixed"


def class_signature(body: dict[str, Any]) -> str:
    """Нормализованная сигнатура класса сборки — ключ ранжирования.

    Собирается из классовых атрибутов, а не из названия: две сборки с разными
    объёмами ОЗУ обязаны попасть в разные сигнатуры, иначе дешевейшая «побеждает»
    за счёт меньшего объёма.
    """
    def _num(field: str) -> str:
        value = _num_or(body.get(field), None)
        if value is None:
            return "-"
        return str(int(value)) if float(value).is_integer() else str(value)

    def _text(field: str) -> str:
        return str(body.get(field) or "").strip().casefold() or "-"

    parts = [
        f"cpu{_num('cpu_cores')}",
        f"ram{_num('ram_gb')}",
        _text("storage_kind"),
        f"sto{_num('storage_gb')}",
        _text("gpu_class"),
        _text("budget_tier"),
    ]
    return "|".join(parts)


def _qty_of(raw: Any) -> float:
    """Количество слота: целое ≥ 1; мусор и 0 дают 1."""
    value = _num_or(raw, None)
    if value is None or value < 1:
        return 1.0
    return float(int(value))


class ReadyBuildsPipelineService:
    """Резолв цен каталога сборок + пересчёт составов и итогов."""

    def __init__(self, *, session: Any) -> None:
        self._session = session

    async def run(self, io: ModuleRowIO, *, resolve: bool = True) -> dict[str, Any]:
        stats: dict[str, Any] = {
            "kind": "equipment.ready_builds",
            "resolve": bool(resolve),
            "groups": 0,
            "items": 0,
            "builds": 0,
            "slots": 0,
            "items_updated": 0,
            "slots_updated": 0,
            "builds_updated": 0,
            "groups_updated": 0,
            "unresolved_slots": 0,
        }

        groups = [r for r in await io.list(GROUPS_TABLE) if isinstance(r, dict)]
        items = [r for r in await io.list(POOL_TABLE) if isinstance(r, dict)]
        builds = [r for r in await io.list(BUILDS_TABLE) if isinstance(r, dict)]
        slots = [r for r in await io.list(SLOTS_TABLE) if isinstance(r, dict)]
        stats["groups"] = len(groups)
        stats["items"] = len(items)
        stats["builds"] = len(builds)
        stats["slots"] = len(slots)
        if not items and not slots:
            return stats

        sellers = [r for r in await io.list(SELLERS_TABLE) if isinstance(r, dict)]
        registry = _SellerRegistry(sellers)

        # ------------------------------------------------ резолв цен из OS
        best_by_item: dict[str, dict[str, Any]] = {}
        best_by_slot_key: dict[str, dict[str, Any]] = {}
        if resolve:
            best_by_item, best_by_slot_key = await self._resolve_prices(
                io, items=items, slots=slots, registry=registry
            )

        now = datetime.now(UTC).isoformat(timespec="seconds")

        # ------------------------------------------------ пул: цены и классы
        item_best: dict[str, dict[str, Any]] = {}
        for item in items:
            iid = str(item.get("row_id") or "")
            body = dict(item.get("body") or {})
            best = best_by_item.get(iid)
            updates: dict[str, Any] = {}
            want_count = int((best or {}).get("offers_count") or 0)
            if int(_num_or(body.get("offers_count"), 0) or 0) != want_count:
                updates["offers_count"] = want_count
            for field, key in (
                ("best_price", "price"),
                ("best_title", "title"),
                ("best_seller", "seller"),
            ):
                want = (best or {}).get(key)
                if field == "best_price":
                    if _num_or(body.get(field), None) != _num_or(want, None):
                        updates[field] = want
                elif str(body.get(field) or "") != str(want or ""):
                    updates[field] = want or ""
            want_stock = bool((best or {}).get("in_stock"))
            if bool(body.get("in_stock")) is not want_stock:
                updates["in_stock"] = want_stock
            if updates:
                updates["synced_at"] = now
                body.update(updates)
                await io.update(POOL_TABLE, iid, body)
                item["body"] = body
                stats["items_updated"] += 1
            item_best[iid] = body

        # дешевейший в классе: строго внутри (group_id, type_id, class_key)
        by_class: dict[tuple[str, str, str], list[str]] = {}
        for iid, body in item_best.items():
            if _num_or(body.get("best_price"), None) is None:
                continue
            if int(_num_or(body.get("offers_count"), 0) or 0) <= 0:
                continue
            key = (
                str(body.get("group_id") or ""),
                str(body.get("type_id") or ""),
                str(body.get("class_key") or ""),
            )
            by_class.setdefault(key, []).append(iid)

        cheapest_by_class: dict[tuple[str, str, str], str] = {}
        for key, ids in by_class.items():
            ranked = sorted(
                ids,
                key=lambda i: (
                    _num_or(item_best[i].get("best_price"), float("inf")),
                    i,
                ),
            )
            cheapest_by_class[key] = ranked[0]
            alts = max(0, len(ids) - 1)
            for iid in ids:
                body = dict(item_best[iid])
                updates = {}
                want_cheapest = ranked[0] == iid
                if bool(body.get("is_cheapest_in_class")) is not want_cheapest:
                    updates["is_cheapest_in_class"] = want_cheapest
                if _num_or(body.get("alternatives_count"), 0) != alts:
                    updates["alternatives_count"] = alts
                if updates:
                    body.update(updates)
                    await io.update(POOL_TABLE, iid, body)
                    item_best[iid] = body
                    stats["items_updated"] += 1

        # ------------------------------------------------ слоты: dynamic/fixed
        slots_by_build: dict[str, list[dict[str, Any]]] = {}
        resolved_by_slot: dict[str, dict[str, Any]] = {}
        for slot in slots:
            sid = str(slot.get("row_id") or "")
            body = dict(slot.get("body") or {})
            bid = str(body.get("build_id") or "")
            slots_by_build.setdefault(bid, []).append(slot)

            mode = str(body.get("mode") or MODE_DYNAMIC).strip() or MODE_DYNAMIC
            type_id = str(body.get("type_id") or "")
            class_key = str(body.get("class_key") or "")
            pinned = str(body.get("item_id") or "")

            chosen_id = ""
            alternatives = 0
            if mode == MODE_FIXED and pinned and pinned in item_best:
                chosen_id = pinned
            elif pinned and pinned in item_best:
                # dynamic с явной привязкой: стартуем от класса, но берём дешевейший
                cbody = item_best[pinned]
                key = (
                    str(cbody.get("group_id") or ""),
                    str(cbody.get("type_id") or type_id),
                    str(cbody.get("class_key") or class_key),
                )
                ids = by_class.get(key) or []
                alternatives = max(0, len(ids) - 1)
                chosen_id = cheapest_by_class.get(key) or pinned
            if not chosen_id and class_key:
                # dynamic без привязки: ищем дешевейший по классу в группе сборки
                group_id = self._build_group(builds, bid)
                for key, cheap in cheapest_by_class.items():
                    if key[0] == group_id and key[1] == type_id and key[2] == class_key:
                        chosen_id = cheap
                        alternatives = max(0, len(by_class.get(key) or []) - 1)
                        break

            price: float | None = None
            title = ""
            pn = ""
            seller = ""
            in_stock = False
            if chosen_id:
                cbody = item_best.get(chosen_id) or {}
                price = _num_or(cbody.get("best_price"), None)
                title = str(cbody.get("best_title") or "")
                pn = str(cbody.get("part_number") or "")
                seller = str(cbody.get("best_seller") or "")
                in_stock = bool(cbody.get("in_stock"))
            else:
                # собственные ключи слота (компонента нет в пуле группы)
                own = best_by_slot_key.get(sid)
                if own:
                    price = _num_or(own.get("price"), None)
                    title = str(own.get("title") or "")
                    pn = str(own.get("part_number") or "")
                    seller = str(own.get("seller") or "")
                    in_stock = bool(own.get("in_stock"))

            qty = _qty_of(body.get("qty"))
            line_total = round(price * qty, 2) if price is not None else None
            if price is None:
                stats["unresolved_slots"] += 1

            resolved_by_slot[sid] = {
                "item_id": chosen_id,
                "price": price,
                "qty": qty,
                "line_total": line_total,
                "in_stock": in_stock,
            }

            updates = {}
            if str(body.get("resolved_item_id") or "") != chosen_id:
                updates["resolved_item_id"] = chosen_id
            if str(body.get("resolved_title") or "") != title:
                updates["resolved_title"] = title
            if str(body.get("resolved_part_number") or "") != pn:
                updates["resolved_part_number"] = pn
            if str(body.get("resolved_seller") or "") != seller:
                updates["resolved_seller"] = seller
            if _num_or(body.get("resolved_price"), None) != price:
                updates["resolved_price"] = price
            if _num_or(body.get("line_total"), None) != line_total:
                updates["line_total"] = line_total
            if bool(body.get("in_stock")) is not in_stock:
                updates["in_stock"] = in_stock
            if _num_or(body.get("alternatives_count"), 0) != alternatives:
                updates["alternatives_count"] = alternatives
            if updates:
                updates["synced_at"] = now
                body.update(updates)
                await io.update(SLOTS_TABLE, sid, body)
                slot["body"] = body
                stats["slots_updated"] += 1

        # ------------------------------------------------ итоги сборок
        build_price: dict[str, float] = {}
        for build in builds:
            bid = str(build.get("row_id") or "")
            body = dict(build.get("body") or {})
            build_slots = slots_by_build.get(bid) or []
            slots_count = len(build_slots)
            qty_total = 0.0
            total = 0.0
            unresolved = 0
            any_on_order = False
            prices: list[float] = []
            for slot in build_slots:
                sid = str(slot.get("row_id") or "")
                res = resolved_by_slot.get(sid) or {}
                qty = float(res.get("qty") or 1.0)
                qty_total += qty
                price = res.get("price")
                if price is None:
                    unresolved += 1
                    continue
                total += float(price) * qty
                prices.append(float(price) * qty)
                if res.get("in_stock") is not True:
                    any_on_order = True
            total = round(total, 2)
            build_price[bid] = total

            signature = class_signature(body)
            updates: dict[str, Any] = {}
            if _num_or(body.get("slots_count"), 0) != slots_count:
                updates["slots_count"] = slots_count
            if _num_or(body.get("qty_total"), 0) != qty_total:
                updates["qty_total"] = qty_total
            if _num_or(body.get("price_total"), None) != total:
                updates["price_total"] = total
            if _num_or(body.get("unresolved_count"), 0) != unresolved:
                updates["unresolved_count"] = unresolved
            if bool(body.get("on_order")) is not any_on_order:
                updates["on_order"] = any_on_order
            if str(body.get("class_signature") or "") != signature:
                updates["class_signature"] = signature
            if updates:
                updates["synced_at"] = now
                body.update(updates)
                await io.update(BUILDS_TABLE, bid, body)
                build["body"] = body
                stats["builds_updated"] += 1

        # «дешевлейшая в классе» — строго внутри (group_id, class_signature)
        priced_by_class: dict[tuple[str, str], list[str]] = {}
        for build in builds:
            body = build.get("body") or {}
            price = _num_or(body.get("price_total"), None)
            if price is None or price <= 0:
                continue
            key = (
                str(body.get("group_id") or ""),
                str(body.get("class_signature") or ""),
            )
            priced_by_class.setdefault(key, []).append(str(build.get("row_id") or ""))

        for key, ids in priced_by_class.items():
            ranked = sorted(ids, key=lambda b: (build_price.get(b, float("inf")), b))
            alts = max(0, len(ids) - 1)
            for bid in ids:
                build = next((b for b in builds if str(b.get("row_id") or "") == bid), None)
                if build is None:
                    continue
                body = dict(build.get("body") or {})
                updates = {}
                want_cheapest = ranked[0] == bid
                if bool(body.get("is_cheapest_in_class")) is not want_cheapest:
                    updates["is_cheapest_in_class"] = want_cheapest
                if _num_or(body.get("alternatives_count"), 0) != alts:
                    updates["alternatives_count"] = alts
                if updates:
                    body.update(updates)
                    await io.update(BUILDS_TABLE, bid, body)
                    build["body"] = body
                    stats["builds_updated"] += 1

        # ------------------------------------------------ агрегаты групп
        builds_by_group: dict[str, list[dict[str, Any]]] = {}
        for build in builds:
            gid = str((build.get("body") or {}).get("group_id") or "")
            if gid:
                builds_by_group.setdefault(gid, []).append(build)
        items_by_group: dict[str, int] = {}
        for item in items:
            gid = str((item.get("body") or {}).get("group_id") or "")
            items_by_group[gid] = items_by_group.get(gid, 0) + 1

        for group in groups:
            gid = str(group.get("row_id") or "")
            body = dict(group.get("body") or {})
            group_builds = builds_by_group.get(gid) or []
            prices = [
                _num_or((b.get("body") or {}).get("price_total"), None) for b in group_builds
            ]
            priced = [p for p in prices if p is not None and p > 0]
            updates: dict[str, Any] = {}
            want_items = items_by_group.get(gid, 0)
            if _num_or(body.get("items_count"), 0) != want_items:
                updates["items_count"] = want_items
            if _num_or(body.get("builds_count"), 0) != len(group_builds):
                updates["builds_count"] = len(group_builds)
            if _num_or(body.get("price_min"), None) != (min(priced) if priced else None):
                updates["price_min"] = min(priced) if priced else None
            if _num_or(body.get("price_max"), None) != (max(priced) if priced else None):
                updates["price_max"] = max(priced) if priced else None
            if updates:
                updates["synced_at"] = now
                body.update(updates)
                await io.update(GROUPS_TABLE, gid, body)
                group["body"] = body
                stats["groups_updated"] += 1

        return stats

    # ------------------------------------------------------------ резолв цен

    async def _resolve_prices(
        self,
        io: ModuleRowIO,
        *,
        items: list[dict[str, Any]],
        slots: list[dict[str, Any]],
        registry: _SellerRegistry,
    ) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
        """Один батч-запрос в OS на все ключи пула и «собственные» ключи слотов."""
        catalogs = [r for r in await io.list(CATALOGS_TABLE) if isinstance(r, dict)]
        ready_ids = [
            str(c.get("row_id"))
            for c in catalogs
            if str((c.get("body") or {}).get("status") or "").strip().lower() == "ready"
        ]
        if not ready_ids:
            return {}, {}

        # слоты с собственными ключами (компонента нет в пуле группы)
        slot_bodies: dict[str, dict[str, Any]] = {}
        for slot in slots:
            sid = str(slot.get("row_id") or "")
            body = slot.get("body") or {}
            has_own = any(
                str(body.get(k) or "").strip()
                for k in ("part_number", "aliases_pn", "aliases_hash")
            )
            if has_own:
                slot_bodies[sid] = body

        owner_bodies: dict[str, dict[str, Any]] = {}
        for item in items:
            iid = str(item.get("row_id") or "")
            if iid:
                owner_bodies[f"i:{iid}"] = item.get("body") or {}
        for sid, body in slot_bodies.items():
            owner_bodies[f"s:{sid}"] = body

        docs = await search_catalog_docs(
            self._session, ready_ids=ready_ids, bodies=list(owner_bodies.values())
        )
        if not docs:
            return {}, {}

        # индексируем доки по владельцу ключей: порядок bodies совпадает с
        # порядком owner_bodies, поэтому zip даёт соответствие.
        keys = list(owner_bodies.keys())
        per_owner = assign_docs_to_keys(
            docs, {k: owner_bodies[k] for k in keys}, registry=registry
        )

        best_by_item: dict[str, dict[str, Any]] = {}
        best_by_slot: dict[str, dict[str, Any]] = {}
        for owner_key, owner_docs in per_owner.items():
            best = await best_price_from_docs(owner_docs, registry=registry)
            if best is None:
                continue
            if owner_key.startswith("i:"):
                best_by_item[owner_key[2:]] = best
            elif owner_key.startswith("s:"):
                best_by_slot[owner_key[2:]] = best
        return best_by_item, best_by_slot

    @staticmethod
    def _build_group(builds: list[dict[str, Any]], build_id: str) -> str:
        for b in builds:
            if str(b.get("row_id") or "") == build_id:
                return str((b.get("body") or {}).get("group_id") or "")
        return ""
