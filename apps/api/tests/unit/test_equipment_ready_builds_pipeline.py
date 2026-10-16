"""WAVE12 — пайплайн каталога «Готовые сборки» v2 (fake IO + fake OpenSearch).

Проверяет ключевые инварианты новой модели:
- карточки резолвят ключи в лидер-цену (наличие → приоритет → цена);
- слот подбирает карточки по constraints (разные группы характеристик не
  конкурируют: «SSD 256» никогда не выиграет у «SSD 512» ценой);
- автоподбор берёт дешевейшую подходящую карточку и дрейфует при смене цен;
- закреплённая card_id перекрывает автоподбор;
- матрица совместимости: домены (socket/ram_type/form_factor) сходятся —
  конфликт фиксируется в compat_issues;
- qty умножает сумму слота и итог сборки; плашки/диски — из слотов;
- целевой коридор цены: умный перебор комбинаций внутри [min, max];
- guard от write-amplification (повторный прогон молчит).
"""

from __future__ import annotations

from typing import Any

import pytest

import prodavan.application.modules.equipment_ready_builds_service as rb_mod
from prodavan.application.modules.equipment_offers_service import ModuleRowIO
from prodavan.application.modules.equipment_ready_builds_service import (
    ReadyBuildsPipelineService,
    attrs_label,
    card_matches_constraints,
    parse_type_schemas,
    validate_card_attrs,
)


class FakeIO(ModuleRowIO):
    """In-memory row store; same call surface as the real IO."""

    def __init__(self, tables: dict[str, list[dict[str, Any]]]) -> None:  # noqa: D107
        self._tables = {t: list(rows) for t, rows in tables.items()}
        self._seq = 0
        self.updated: list[tuple[str, str]] = []

    async def list(self, table_slug: str) -> list[dict[str, Any]]:
        return [dict(r) for r in self._tables.get(table_slug, [])]

    async def list_project_wide(self, table_slug: str) -> list[dict[str, Any]]:
        return await self.list(table_slug)

    async def create(self, table_slug: str, body: dict[str, Any]) -> dict[str, Any]:
        self._seq += 1
        row = {"row_id": f"row_{self._seq}", "body": dict(body)}
        self._tables.setdefault(table_slug, []).append(row)
        return dict(row)

    async def update(
        self, table_slug: str, row_id: str, body: dict[str, Any]
    ) -> dict[str, Any]:
        for row in self._tables.get(table_slug, []):
            if row["row_id"] == row_id:
                row["body"] = dict(body)
                self.updated.append((table_slug, row_id))
                return dict(row)
        raise AssertionError(f"update of missing row {table_slug}/{row_id}")

    async def delete(self, table_slug: str, row_id: str) -> bool:
        rows = self._tables.get(table_slug, [])
        before = len(rows)
        self._tables[table_slug] = [r for r in rows if r["row_id"] != row_id]
        return len(self._tables[table_slug]) != before

    def body(self, table_slug: str, row_id: str) -> dict[str, Any]:
        return dict(
            next(r for r in self._tables[table_slug] if r["row_id"] == row_id)["body"]
        )


def _doc(pn: str, *, price: float, supplier: str, in_stock: bool = True, h: str = "") -> dict:
    return {
        "src_hash": h or f"h_{pn}_{supplier}",
        "part_number": pn,
        "title": f"{pn} от {supplier}",
        "supplier": supplier,
        "price": str(price),
        "price_num": price,
        "currency": "RUB",
        "in_stock": in_stock,
    }


# Каталог: CPU-A дешевле у Петрова; RAM-16B дешевле RAM-16 (обе 16 ГБ DDR4);
# CPU-AM5 дороже и другого сокета; SSD-512 есть, SSD-256 тоже; охлаждения нет.
DOCS = [
    _doc("CPU-A", price=12000.0, supplier="Иванов"),
    _doc("CPU-A", price=11000.0, supplier="Петров"),
    _doc("CPU-B", price=15000.0, supplier="Иванов"),
    _doc("CPU-AM5", price=20000.0, supplier="Иванов"),
    _doc("MB-LGA", price=8000.0, supplier="Иванов"),
    _doc("MB-AM5", price=9000.0, supplier="Иванов"),
    _doc("RAM-16", price=3000.0, supplier="Иванов"),
    _doc("RAM-16B", price=2800.0, supplier="Петров"),
    _doc("RAM-32", price=5500.0, supplier="Иванов"),
    _doc("SSD-256", price=3500.0, supplier="Иванов"),
    _doc("SSD-512", price=5000.0, supplier="Петров", h="h_ssd512"),
    _doc("SSD-512P", price=4600.0, supplier="Иванов", h="h_ssd512p"),
    _doc("PSU-650", price=6000.0, supplier="Иванов"),
]

TYPES = [
    {
        "row_id": "etype_cpu",
        "body": {
            "name": "Процессор",
            "sort_order": 10,
            "fields_json": [
                {
                    "key": "socket",
                    "label": {"ru": "Сокет"},
                    "kind": "enum",
                    "required": True,
                    "compat_domain": "socket",
                    "values": ["AM4", "AM5", "LGA1700"],
                },
                {
                    "key": "cores",
                    "label": {"ru": "Ядра"},
                    "kind": "groups",
                    "required": True,
                    "groups": [
                        {"value": "4", "label": "4 ядра"},
                        {"value": "6", "label": "6 ядер"},
                        {"value": "8", "label": "8 ядер"},
                    ],
                },
            ],
        },
    },
    {
        "row_id": "etype_motherboard",
        "body": {
            "name": "Материнская плата",
            "sort_order": 20,
            "fields_json": [
                {
                    "key": "socket",
                    "label": {"ru": "Сокет"},
                    "kind": "enum",
                    "required": True,
                    "compat_domain": "socket",
                    "values": ["AM4", "AM5", "LGA1700"],
                },
                {
                    "key": "ram_type",
                    "label": {"ru": "Тип ОЗУ"},
                    "kind": "enum",
                    "required": True,
                    "compat_domain": "ram_type",
                    "values": ["DDR4", "DDR5"],
                },
            ],
        },
    },
    {
        "row_id": "etype_ram",
        "body": {
            "name": "Оперативная память",
            "sort_order": 30,
            "fields_json": [
                {
                    "key": "ram_type",
                    "label": {"ru": "Тип ОЗУ"},
                    "kind": "enum",
                    "required": True,
                    "compat_domain": "ram_type",
                    "values": ["DDR4", "DDR5"],
                },
                {
                    "key": "module_capacity",
                    "label": {"ru": "Объём модуля"},
                    "kind": "groups",
                    "required": True,
                    "groups": [
                        {"value": "16", "label": "16 ГБ"},
                        {"value": "32", "label": "32 ГБ"},
                    ],
                },
            ],
        },
    },
    {
        "row_id": "etype_storage",
        "body": {
            "name": "Накопитель",
            "sort_order": 40,
            "fields_json": [
                {
                    "key": "drive_type",
                    "label": {"ru": "Тип"},
                    "kind": "enum",
                    "required": True,
                    "values": ["SSD", "HDD"],
                },
                {
                    "key": "capacity",
                    "label": {"ru": "Объём"},
                    "kind": "groups",
                    "required": True,
                    "groups": [
                        {"value": "256", "label": "256 ГБ"},
                        {"value": "512", "label": "512 ГБ"},
                    ],
                },
            ],
        },
    },
    {
        "row_id": "etype_psu",
        "body": {
            "name": "Блок питания",
            "sort_order": 60,
            "fields_json": [
                {
                    "key": "wattage",
                    "label": {"ru": "Мощность"},
                    "kind": "groups",
                    "required": True,
                    "groups": [{"value": "650", "label": "650 Вт"}],
                },
            ],
        },
    },
    {
        "row_id": "etype_cooling",
        "body": {
            "name": "Охлаждение",
            "sort_order": 70,
            "fields_json": [
                {
                    "key": "socket",
                    "label": {"ru": "Сокет"},
                    "kind": "enum",
                    "required": True,
                    "compat_domain": "socket",
                    "values": ["AM4", "AM5", "LGA1700"],
                },
            ],
        },
    },
]


def _card(
    row_id: str,
    type_id: str,
    pn: str,
    attrs: dict[str, str],
    *,
    aliases_hash: str = "",
) -> dict[str, Any]:
    body: dict[str, Any] = {"type_id": type_id, "part_number": pn, "attrs": attrs}
    if aliases_hash:
        body["aliases_hash"] = aliases_hash
    return {"row_id": row_id, "body": body}


def _slot(
    row_id: str,
    build_id: str,
    type_id: str,
    constraints: dict[str, str] | None = None,
    *,
    qty: int = 1,
    card_id: str = "",
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "build_id": build_id,
        "type_id": type_id,
        "constraints": constraints or {},
        "qty": qty,
    }
    if card_id:
        body["card_id"] = card_id
    return {"row_id": row_id, "body": body}


def _tables(
    *,
    cards: list[dict[str, Any]] | None = None,
    builds: list[dict[str, Any]] | None = None,
    slots: list[dict[str, Any]] | None = None,
) -> dict[str, list[dict[str, Any]]]:
    return {
        "catalogs": [
            {"row_id": "cat_1", "body": {"name": "Test", "status": "ready", "paused": False}}
        ],
        "trusted_sellers": [
            {"row_id": "s1", "body": {"name": "Иванов", "is_enabled": True}},
            {"row_id": "s2", "body": {"name": "Петров", "is_enabled": True}},
        ],
        "equipment_types": TYPES,
        "part_cards": cards or [],
        "ready_builds": builds or [],
        "ready_build_slots": slots or [],
    }


def _build(row_id: str, name: str = "Офисный ПК", **extra: Any) -> dict[str, Any]:
    body: dict[str, Any] = {"name": name, "build_kind": "pc", "is_enabled": True}
    body.update(extra)
    return {"row_id": row_id, "body": body}


def _cards_default() -> list[dict[str, Any]]:
    return [
        _card("c_cpu_a", "etype_cpu", "CPU-A", {"socket": "LGA1700", "cores": "6"}),
        _card("c_cpu_b", "etype_cpu", "CPU-B", {"socket": "LGA1700", "cores": "8"}),
        _card("c_cpu_am5", "etype_cpu", "CPU-AM5", {"socket": "AM5", "cores": "8"}),
        _card("c_mb_lga", "etype_motherboard", "MB-LGA", {"socket": "LGA1700", "ram_type": "DDR4"}),
        _card("c_mb_am5", "etype_motherboard", "MB-AM5", {"socket": "AM5", "ram_type": "DDR5"}),
        _card("c_ram16", "etype_ram", "RAM-16", {"ram_type": "DDR4", "module_capacity": "16"}),
        _card("c_ram16b", "etype_ram", "RAM-16B", {"ram_type": "DDR4", "module_capacity": "16"}),
        _card("c_ram32", "etype_ram", "RAM-32", {"ram_type": "DDR4", "module_capacity": "32"}),
        _card("c_ssd256", "etype_storage", "SSD-256", {"drive_type": "SSD", "capacity": "256"}),
        _card("c_ssd512", "etype_storage", "SSD-512", {"drive_type": "SSD", "capacity": "512"}, aliases_hash="h_ssd512"),
        _card("c_ssd512p", "etype_storage", "SSD-512P", {"drive_type": "SSD", "capacity": "512"}, aliases_hash="h_ssd512p"),
        _card("c_psu", "etype_psu", "PSU-650", {"wattage": "650"}),
    ]


def _patch_docs(monkeypatch: pytest.MonkeyPatch, docs: list[dict[str, Any]]) -> None:
    async def fake_search(session: Any, *, ready_ids: list[str], bodies: list[dict[str, Any]]):
        return docs

    monkeypatch.setattr(rb_mod, "search_catalog_docs", fake_search)


async def _run(io: FakeIO) -> dict[str, Any]:
    return await ReadyBuildsPipelineService(session=object()).run(io)


# ------------------------------------------------------------------ карточки


@pytest.mark.asyncio
async def test_cards_resolve_price_leader(monkeypatch: pytest.MonkeyPatch) -> None:
    """Карточка получает лидер-цену: наличие → цена (CPU-A дешевле у Петрова)."""
    _patch_docs(monkeypatch, DOCS)
    io = FakeIO(_tables(cards=_cards_default()))
    stats = await _run(io)
    assert stats["cards"] == 12
    assert stats["cards_updated"] > 0
    body = io.body("part_cards", "c_cpu_a")
    assert body["best_price"] == 11000.0
    assert body["best_seller"] == "Петров"
    assert body["in_stock"] is True
    assert body["offers_count"] == 2
    assert body["type_name"] == "Процессор"
    assert body["attrs_label"] == "LGA1700 · 6 ядер"


@pytest.mark.asyncio
async def test_card_without_offers_stays_unpriced(monkeypatch: pytest.MonkeyPatch) -> None:
    """Карточка без офферов в каталоге — без цены, слот с ней не разрешается."""
    _patch_docs(monkeypatch, DOCS)
    cards = [_card("c_ghost", "etype_cooling", "COOL-X", {"socket": "LGA1700"})]
    io = FakeIO(_tables(cards=cards))
    await _run(io)
    body = io.body("part_cards", "c_ghost")
    assert body.get("best_price") is None
    assert body.get("offers_count", 0) == 0


# ------------------------------------------------------------------ слоты


@pytest.mark.asyncio
async def test_slot_picks_cheapest_matching_constraints(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Слот «SSD 512» видит только 512-карточки и берёт дешевейшую (SSD-512P)."""
    _patch_docs(monkeypatch, DOCS)
    io = FakeIO(
        _tables(
            cards=_cards_default(),
            builds=[_build("b1")],
            slots=[
                _slot("sl_sto", "b1", "etype_storage", {"drive_type": "SSD", "capacity": "512"}),
            ],
        )
    )
    await _run(io)
    body = io.body("ready_build_slots", "sl_sto")
    assert body["resolved_card_id"] == "c_ssd512p"  # 4600 < 5000
    assert body["resolved_price"] == 4600.0
    assert body["alternatives_count"] == 1  # SSD-512 остаётся альтернативой
    assert body["constraints_label"] == "SSD · 512 ГБ"
    build = io.body("ready_builds", "b1")
    assert build["price_total"] == 4600.0
    assert build["price_min"] == 4600.0
    assert build["price_max"] == 5000.0
    assert build["spec_summary"] == "Накопитель: SSD · 512 ГБ"


@pytest.mark.asyncio
async def test_price_drift_moves_auto_pick(monkeypatch: pytest.MonkeyPatch) -> None:
    """Смена лидера цены в каталоге переводит автоподбор на нового лидера."""
    io = FakeIO(
        _tables(
            cards=_cards_default(),
            builds=[_build("b1")],
            slots=[_slot("sl_sto", "b1", "etype_storage", {"capacity": "512"})],
        )
    )
    _patch_docs(monkeypatch, DOCS)
    await _run(io)
    assert io.body("ready_build_slots", "sl_sto")["resolved_card_id"] == "c_ssd512p"

    # SSD-512 подешевел ниже SSD-512P → автоподбор переехал
    docs2 = [d if d["part_number"] != "SSD-512" else {**d, "price_num": 4000.0, "price": "4000.0"} for d in DOCS]
    _patch_docs(monkeypatch, docs2)
    await _run(io)
    assert io.body("ready_build_slots", "sl_sto")["resolved_card_id"] == "c_ssd512"


@pytest.mark.asyncio
async def test_pinned_card_wins_over_cheaper(monkeypatch: pytest.MonkeyPatch) -> None:
    """Закреплённая card_id — ручная замена: дешевле ≠ выбрано."""
    _patch_docs(monkeypatch, DOCS)
    io = FakeIO(
        _tables(
            cards=_cards_default(),
            builds=[_build("b1")],
            slots=[
                _slot(
                    "sl_sto",
                    "b1",
                    "etype_storage",
                    {"capacity": "512"},
                    card_id="c_ssd512",  # 5000, хотя SSD-512P за 4600 дешевле
                ),
            ],
        )
    )
    await _run(io)
    body = io.body("ready_build_slots", "sl_sto")
    assert body["resolved_card_id"] == "c_ssd512"
    assert body["resolved_price"] == 5000.0


# ------------------------------------------------------------------ совместимость


@pytest.mark.asyncio
async def test_compat_domain_filters_candidates(monkeypatch: pytest.MonkeyPatch) -> None:
    """Сокет CPU-СЛОТА сужает кандидатов материнки: AM5-плата не подставится."""
    _patch_docs(monkeypatch, DOCS)
    io = FakeIO(
        _tables(
            cards=_cards_default(),
            builds=[_build("b1")],
            slots=[
                _slot("sl_cpu", "b1", "etype_cpu", {"socket": "LGA1700"}),
                _slot("sl_mb", "b1", "etype_motherboard"),  # без constraints
            ],
        )
    )
    await _run(io)
    mb = io.body("ready_build_slots", "sl_mb")
    assert mb["resolved_card_id"] == "c_mb_lga"  # не c_mb_am5
    build = io.body("ready_builds", "b1")
    assert build.get("compat_ok", True) is True
    # доменных конфликтов нет (заметки о комплектности — отдельная тема)
    assert "socket" not in (build.get("compat_issues") or "")


@pytest.mark.asyncio
async def test_compat_conflict_is_reported_not_blocking(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Явный конфликт constraints (LGA1700 + AM5) — сборка собирается, но с issue."""
    _patch_docs(monkeypatch, DOCS)
    io = FakeIO(
        _tables(
            cards=_cards_default(),
            builds=[_build("b1")],
            slots=[
                _slot("sl_cpu", "b1", "etype_cpu", {"socket": "LGA1700"}),
                _slot("sl_mb", "b1", "etype_motherboard", {"socket": "AM5"}),
            ],
        )
    )
    await _run(io)
    build = io.body("ready_builds", "b1")
    assert build["compat_ok"] is False
    assert "socket" in build["compat_issues"]


@pytest.mark.asyncio
async def test_ram_domain_carries_from_motherboard(monkeypatch: pytest.MonkeyPatch) -> None:
    """Тип ОЗУ материнки фиксирует домен: DDR5-модули не попадут в сборку DDR4."""
    _patch_docs(monkeypatch, DOCS)
    cards = _cards_default() + [
        _card("c_ram_d5", "etype_ram", "RAM-16", {"ram_type": "DDR5", "module_capacity": "16"}),
    ]
    io = FakeIO(
        _tables(
            cards=cards,
            builds=[_build("b1")],
            slots=[
                _slot("sl_mb", "b1", "etype_motherboard", {"ram_type": "DDR4"}),
                _slot("sl_ram", "b1", "etype_ram"),  # без constraints
            ],
        )
    )
    await _run(io)
    ram = io.body("ready_build_slots", "sl_ram")
    assert ram["resolved_card_id"] in ("c_ram16", "c_ram16b")  # DDR4, не DDR5


# ------------------------------------------------------------------ количество и итоги


@pytest.mark.asyncio
async def test_qty_multiplies_slot_and_counts_sticks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """2 плашки ОЗУ: сумма слота ×2, ram_sticks=2, qty_total учитывает."""
    _patch_docs(monkeypatch, DOCS)
    io = FakeIO(
        _tables(
            cards=_cards_default(),
            builds=[_build("b1")],
            slots=[
                _slot("sl_cpu", "b1", "etype_cpu", {"socket": "LGA1700", "cores": "6"}),
                _slot("sl_ram", "b1", "etype_ram", {"ram_type": "DDR4", "module_capacity": "16"}, qty=2),
                _slot("sl_sto", "b1", "etype_storage", {"capacity": "256"}),
            ],
        )
    )
    await _run(io)
    ram = io.body("ready_build_slots", "sl_ram")
    assert ram["resolved_card_id"] == "c_ram16b"  # 2800 < 3000
    assert ram["line_total"] == 5600.0
    build = io.body("ready_builds", "b1")
    assert build["price_total"] == 11000.0 + 5600.0 + 3500.0
    assert build["ram_sticks"] == 2
    assert build["storage_drives"] == 1
    assert build["qty_total"] == 4
    assert build["unresolved_count"] == 0
    assert build.get("on_order") is not True


@pytest.mark.asyncio
async def test_unresolved_slot_counted_and_excluded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Слот без подходящих карточек — в unresolved_count, цена без него."""
    _patch_docs(monkeypatch, DOCS)
    io = FakeIO(
        _tables(
            cards=_cards_default(),
            builds=[_build("b1")],
            slots=[
                _slot("sl_cpu", "b1", "etype_cpu", {"cores": "6"}),
                _slot("sl_cool", "b1", "etype_cooling", {"socket": "LGA1700"}),  # карточек нет
            ],
        )
    )
    await _run(io)
    build = io.body("ready_builds", "b1")
    assert build["unresolved_count"] == 1
    assert build["price_total"] == 11000.0
    assert "нет карточек" in build["compat_issues"]


@pytest.mark.asyncio
async def test_on_order_when_leader_out_of_stock(monkeypatch: pytest.MonkeyPatch) -> None:
    """Лидер цены не в наличии → слот и сборка «под заказ»."""
    docs = [d if d["part_number"] != "CPU-A" else {**d, "in_stock": False} for d in DOCS]
    _patch_docs(monkeypatch, docs)
    io = FakeIO(
        _tables(
            cards=[_card("c_cpu_a", "etype_cpu", "CPU-A", {"socket": "LGA1700", "cores": "6"})],
            builds=[_build("b1")],
            slots=[_slot("sl_cpu", "b1", "etype_cpu", {"cores": "6"})],
        )
    )
    await _run(io)
    build = io.body("ready_builds", "b1")
    assert build["on_order"] is True
    assert build["price_total"] == 11000.0  # цена есть, наличия нет


# ------------------------------------------------------------------ коридор цен


@pytest.mark.asyncio
async def test_target_max_picks_richer_combo(monkeypatch: pytest.MonkeyPatch) -> None:
    """С потолком цены выбирается максимально наполненная комбинация ≤ max."""
    _patch_docs(monkeypatch, DOCS)
    io = FakeIO(
        _tables(
            cards=_cards_default(),
            builds=[_build("b1", target_price_max=17000.0)],
            slots=[
                _slot("sl_cpu", "b1", "etype_cpu", {"socket": "LGA1700"}),
                _slot("sl_ram", "b1", "etype_ram", {"ram_type": "DDR4"}),
            ],
        )
    )
    await _run(io)
    build = io.body("ready_builds", "b1")
    # дешевейшая: CPU-A (11000) + RAM-16B (2800) = 13800
    # потолок 17000 позволяет RAM-32 (5500): 16500 ≤ 17000 — максимум наполнения
    assert build["price_total"] == 16500.0
    assert io.body("ready_build_slots", "sl_ram")["resolved_card_id"] == "c_ram32"
    assert build["price_note"] == ""
    assert build["price_min"] == 13800.0
    assert build["price_max"] == 20500.0  # CPU-B + RAM-32


@pytest.mark.asyncio
async def test_target_min_upgrades_cheapest(monkeypatch: pytest.MonkeyPatch) -> None:
    """С минимумом цены выбирается дешевейшая комбинация ≥ min."""
    _patch_docs(monkeypatch, DOCS)
    io = FakeIO(
        _tables(
            cards=_cards_default(),
            builds=[_build("b1", target_price_min=15000.0)],
            slots=[
                _slot("sl_cpu", "b1", "etype_cpu", {"socket": "LGA1700"}),
                _slot("sl_ram", "b1", "etype_ram", {"ram_type": "DDR4"}),
            ],
        )
    )
    await _run(io)
    build = io.body("ready_builds", "b1")
    # дешевейшая 13800 < 15000; апгрейд RAM-16B→RAM-32: 16500 ≥ 15000
    assert build["price_total"] == 16500.0
    assert io.body("ready_build_slots", "sl_ram")["resolved_card_id"] == "c_ram32"


@pytest.mark.asyncio
async def test_unreachable_corridor_falls_back_with_note(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Коридор недостижим — ближайшая крайняя конфигурация + честный price_note."""
    _patch_docs(monkeypatch, DOCS)
    io = FakeIO(
        _tables(
            cards=_cards_default(),
            builds=[_build("b1", target_price_max=10000.0)],
            slots=[_slot("sl_cpu", "b1", "etype_cpu", {"socket": "LGA1700"})],
        )
    )
    await _run(io)
    build = io.body("ready_builds", "b1")
    assert build["price_total"] == 11000.0  # дешевейшая, хоть и выше потолка
    assert "недостижим" in build["price_note"]


# ------------------------------------------------------------------ guard от записей


@pytest.mark.asyncio
async def test_second_run_is_silent(monkeypatch: pytest.MonkeyPatch) -> None:
    """Повторный прогон без изменений не переписывает строки (write-amplification)."""
    _patch_docs(monkeypatch, DOCS)
    io = FakeIO(
        _tables(
            cards=_cards_default(),
            builds=[_build("b1")],
            slots=[
                _slot("sl_cpu", "b1", "etype_cpu", {"socket": "LGA1700", "cores": "6"}),
                _slot("sl_ram", "b1", "etype_ram", {"module_capacity": "16"}),
            ],
        )
    )
    await _run(io)
    io.updated.clear()
    stats = await _run(io)
    assert stats["cards_updated"] == 0
    assert stats["slots_updated"] == 0
    assert stats["builds_updated"] == 0
    assert io.updated == []


# ------------------------------------------------------------------ сплит qty


@pytest.mark.asyncio
async def test_ram_split_pair_cheaper_than_single(monkeypatch: pytest.MonkeyPatch) -> None:
    """WAVE13: «32 ГБ» собирается как 2×16, если пара дешевле одного модуля 32."""
    docs = DOCS + [_doc("RAM-16C", price=2600.0, supplier="Иванов")]
    cards = _cards_default() + [
        _card("c_ram16c", "etype_ram", "RAM-16C", {"ram_type": "DDR4", "module_capacity": "16"}),
    ]
    io = FakeIO(
        _tables(
            cards=cards,
            builds=[_build("b1")],
            slots=[_slot("sl_ram", "b1", "etype_ram", {"ram_type": "DDR4", "ram_total": "32"})],
        )
    )
    _patch_docs(monkeypatch, docs)
    await _run(io)
    slot = io.body("ready_build_slots", "sl_ram")
    # 2×16 по 2600 (5200) дешевле 1×32 (5500) — сплит выиграл
    assert slot["qty"] == 2
    assert slot["resolved_card_id"] == "c_ram16c"
    assert slot["line_total"] == 5200.0
    build = io.body("ready_builds", "b1")
    assert build["ram_sticks"] == 2
    assert build["price_total"] == 5200.0
    assert "×2" in build["spec_summary"]


@pytest.mark.asyncio
async def test_ram_no_split_when_single_cheaper(monkeypatch: pytest.MonkeyPatch) -> None:
    """Единый модуль дешевле пары — сплита нет (qty остаётся 1)."""
    _patch_docs(monkeypatch, DOCS)
    io = FakeIO(
        _tables(
            cards=_cards_default(),
            builds=[_build("b1")],
            slots=[_slot("sl_ram", "b1", "etype_ram", {"ram_type": "DDR4", "ram_total": "32"})],
        )
    )
    await _run(io)
    slot = io.body("ready_build_slots", "sl_ram")
    # 1×32 (5500) дешевле 2×16 (5600) и 4×8
    assert slot["qty"] == 1
    assert slot["resolved_card_id"] == "c_ram32"
    assert slot["line_total"] == 5500.0


@pytest.mark.asyncio
async def test_storage_split_combinatorics(monkeypatch: pytest.MonkeyPatch) -> None:
    """Тот же сплит для дисков: storage_total распадается на кратные ёмкости."""
    docs = DOCS + [_doc("SSD-128", price=1500.0, supplier="Иванов")]
    cards = _cards_default() + [
        _card("c_ssd128", "etype_storage", "SSD-128", {"drive_type": "SSD", "capacity": "128"}),
    ]
    io = FakeIO(
        _tables(
            cards=cards,
            builds=[_build("b1")],
            slots=[_slot("sl_sto", "b1", "etype_storage", {"drive_type": "SSD", "storage_total": "512"})],
        )
    )
    _patch_docs(monkeypatch, docs)
    await _run(io)
    slot = io.body("ready_build_slots", "sl_sto")
    # 4×128 (6000) дороже 1×512 (4600) — без сплита; 2×256 (7000) тоже дороже
    assert slot["qty"] == 1
    assert slot["resolved_card_id"] == "c_ssd512p"


# ------------------------------------------------------------------ шаблоны типов


def test_parse_type_schemas_reads_groups_and_domains() -> None:
    schemas = parse_type_schemas(TYPES)
    cpu = schemas["etype_cpu"]
    assert cpu["name"] == "Процессор"
    assert cpu["fields"]["socket"]["compat_domain"] == "socket"
    assert cpu["fields"]["socket"]["required"] is True
    assert cpu["fields"]["cores"]["choices"] == {"4": "4 ядра", "6": "6 ядер", "8": "8 ядер"}
    # старый формат (key/label) читается как text
    legacy = parse_type_schemas(
        [{"row_id": "t_x", "body": {"name": "X", "fields_json": [{"key": "a", "label": "A"}]}}]
    )
    assert legacy["t_x"]["fields"]["a"]["kind"] == "text"
    assert legacy["t_x"]["fields"]["a"]["choices"] == {}


def test_attrs_label_uses_group_labels() -> None:
    schemas = parse_type_schemas(TYPES)
    fields = schemas["etype_storage"]["fields"]
    assert attrs_label(fields, {"drive_type": "SSD", "capacity": "512"}) == "SSD · 512 ГБ"
    # значение вне шаблона показывается как есть
    assert attrs_label(fields, {"drive_type": "NVMe"}) == "NVMe"


def test_card_matches_constraints_normalizes_case() -> None:
    assert card_matches_constraints({"capacity": "512"}, {"capacity": "512"}, {}) is True
    assert card_matches_constraints({"capacity": "512"}, {"capacity": "256"}, {}) is False
    assert card_matches_constraints({"socket": "lga1700"}, {"socket": "LGA1700"}, {}) is True
    # пустые constraints подходят всем
    assert card_matches_constraints({}, {"capacity": "512"}, {}) is False
    assert card_matches_constraints({"capacity": "512"}, {}, {}) is True


def test_validate_card_attrs_required_and_groups() -> None:
    schemas = parse_type_schemas(TYPES)
    fields = schemas["etype_cpu"]["fields"]
    errors = validate_card_attrs(fields, {"socket": "LGA1700"})
    assert any("Ядра" in e for e in errors)  # cores required
    errors = validate_card_attrs(fields, {"socket": "LGA9999", "cores": "6"})
    assert any("вне групп" in e for e in errors)
    assert validate_card_attrs(fields, {"socket": "AM5", "cores": "8"}) == []
