"""WAVE11 — пайплайн каталога «Готовые сборки» (fake IO + fake OpenSearch).

Проверяет ключевые инварианты каталога:
- резолв ключей пула в лучшую цену (наличие → приоритет → цена, как у офферов);
- «дешевлейший в классе» строго внутри (group_id, type_id, class_key);
- dynamic-слот дрейфует на дешевейший компонент класса, fixed — нет;
- qty умножает сумму слота и итог сборки;
- class_signature разделяет сборки: 16 ГБ не «побеждает» 32 ГБ ценой;
- агрегаты группы и guard от write-amplification (повторный прогон молчит).
"""

from __future__ import annotations

from typing import Any

import pytest

import prodavan.application.modules.equipment_ready_builds_service as rb_mod
from prodavan.application.modules.equipment_offers_service import ModuleRowIO
from prodavan.application.modules.equipment_ready_builds_service import (
    ReadyBuildsPipelineService,
    class_signature,
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


# Каталог: CPU-A дешевле у Петрова; RAM-16B дешевле RAM-16 (тот же класс);
# CPU-ORDER есть только «под заказ».
DOCS = [
    _doc("CPU-A", price=12000.0, supplier="Иванов"),
    _doc("CPU-A", price=11000.0, supplier="Петров"),
    _doc("CPU-B", price=15000.0, supplier="Иванов"),
    _doc("CPU-ORDER", price=9000.0, supplier="Иванов", in_stock=False),
    _doc("RAM-16", price=3000.0, supplier="Иванов"),
    _doc("RAM-16B", price=2800.0, supplier="Петров"),
    _doc("RAM-32", price=5500.0, supplier="Иванов"),
    _doc("SSD-256", price=4000.0, supplier="Петров", h="h_ssd256"),
]


def _tables() -> dict[str, list[dict[str, Any]]]:
    return {
        "catalogs": [
            {"row_id": "cat_1", "body": {"name": "Test", "status": "ready", "paused": False}}
        ],
        "trusted_sellers": [
            {"row_id": "s1", "body": {"name": "Иванов", "is_enabled": True}},
            {"row_id": "s2", "body": {"name": "Петров", "is_enabled": True}},
        ],
        "build_groups": [
            {
                "row_id": "grp_am4",
                "body": {
                    "name": "AM4 · DDR4 · бюджетный",
                    "build_kind": "pc",
                    "socket": "AM4",
                    "ram_type": "DDR4",
                    "budget_tier": "budget",
                    "is_enabled": True,
                },
            }
        ],
        "build_group_items": [
            {
                "row_id": "it_cpu_a",
                "body": {
                    "group_id": "grp_am4",
                    "type_id": "etype_cpu",
                    "class_key": "cpu_6c",
                    "class_label": "6 ядер",
                    "part_number": "CPU-A",
                },
            },
            {
                "row_id": "it_cpu_b",
                "body": {
                    "group_id": "grp_am4",
                    "type_id": "etype_cpu",
                    "class_key": "cpu_6c",
                    "class_label": "6 ядер",
                    "part_number": "CPU-B",
                },
            },
            {
                "row_id": "it_ram16",
                "body": {
                    "group_id": "grp_am4",
                    "type_id": "etype_ram",
                    "class_key": "ram_16",
                    "class_label": "16 ГБ DDR4",
                    "part_number": "RAM-16",
                },
            },
            {
                "row_id": "it_ram16b",
                "body": {
                    "group_id": "grp_am4",
                    "type_id": "etype_ram",
                    "class_key": "ram_16",
                    "class_label": "16 ГБ DDR4",
                    "part_number": "RAM-16B",
                },
            },
            {
                "row_id": "it_ram32",
                "body": {
                    "group_id": "grp_am4",
                    "type_id": "etype_ram",
                    "class_key": "ram_32",
                    "class_label": "32 ГБ DDR4",
                    "part_number": "RAM-32",
                },
            },
            # без офферов в каталоге — слот с ним останется неразрешённым
            {
                "row_id": "it_ghost",
                "body": {
                    "group_id": "grp_am4",
                    "type_id": "etype_cooling",
                    "class_key": "cool_1",
                    "class_label": "Кулер",
                    "part_number": "NO-SUCH-PN",
                },
            },
        ],
        "ready_builds": [],
        "ready_build_slots": [],
    }


@pytest.fixture()
def io(monkeypatch) -> FakeIO:
    async def _fake_search(session, *, ready_ids, bodies):  # noqa: ANN001
        return list(DOCS)

    monkeypatch.setattr(rb_mod, "search_catalog_docs", _fake_search)
    return FakeIO(_tables())


async def _add_build(
    io: FakeIO,
    *,
    row_id: str,
    name: str,
    ram_gb: int,
    slots: list[dict[str, Any]],
) -> str:
    await io.create(
        "ready_builds",
        {
            "group_id": "grp_am4",
            "name": name,
            "build_kind": "pc",
            "budget_tier": "budget",
            "cpu_cores": 6,
            "ram_gb": ram_gb,
            "storage_kind": "SSD",
            "storage_gb": 256,
            "is_enabled": True,
        },
    )
    # create() сам назначает row_id — пересохраняем под нужным
    created = io._tables["ready_builds"][-1]
    created["row_id"] = row_id
    for slot in slots:
        await io.create("ready_build_slots", {"build_id": row_id, **slot})
    return row_id


async def test_pool_items_resolve_best_price_and_cheapest_in_class(io: FakeIO) -> None:
    svc = ReadyBuildsPipelineService(session=object())
    await svc.run(io)

    cpu_a = io.body("build_group_items", "it_cpu_a")
    # лучший оффер CPU-A: Петров 11000 (дешевле Иванова)
    assert cpu_a["best_price"] == 11000.0
    assert cpu_a["best_seller"] == "Петров"
    assert cpu_a["offers_count"] == 2
    assert cpu_a["in_stock"] is True

    ram16 = io.body("build_group_items", "it_ram16")
    ram16b = io.body("build_group_items", "it_ram16b")
    # один класс ram_16 → дешевейший помечен, у обоих видны альтернативы
    assert ram16b["is_cheapest_in_class"] is True
    assert ram16.get("is_cheapest_in_class") is not True
    assert ram16b["alternatives_count"] == 1
    assert ram16["alternatives_count"] == 1

    # другой класс (ram_32) не конкурирует с ram_16
    ram32 = io.body("build_group_items", "it_ram32")
    assert ram32["is_cheapest_in_class"] is True
    assert ram32.get("alternatives_count", 0) == 0

    # нет офферов → цена не выдумана
    ghost = io.body("build_group_items", "it_ghost")
    assert ghost.get("offers_count", 0) == 0
    # цена не выдумана: ключа нет вовсе либо он пустой
    assert ghost.get("best_price") in (None, 0)


async def test_dynamic_slot_drifts_to_cheapest_fixed_does_not(io: FakeIO) -> None:
    """dynamic берёт дешевейший компонент класса; fixed держит закреплённый."""
    svc = ReadyBuildsPipelineService(session=object())
    await _add_build(
        io,
        row_id="b_dyn",
        name="Динамическая",
        ram_gb=16,
        slots=[
            {"type_id": "etype_ram", "class_key": "ram_16", "mode": "dynamic", "qty": 1},
            {"type_id": "etype_cpu", "class_key": "cpu_6c", "mode": "fixed",
             "item_id": "it_cpu_b", "qty": 1},
        ],
    )
    await svc.run(io)

    slots = {
        s["body"]["type_id"]: s["body"]
        for s in io._tables["ready_build_slots"]
        if s["body"]["build_id"] == "b_dyn"
    }
    ram = slots["etype_ram"]
    cpu = slots["etype_cpu"]
    # dynamic → RAM-16B (2800), а не RAM-16 (3000)
    assert ram["resolved_item_id"] == "it_ram16b"
    assert ram["resolved_price"] == 2800.0
    assert ram["alternatives_count"] == 1
    # fixed → закреплённый CPU-B (15000), хотя CPU-A дешевле (11000)
    assert cpu["resolved_item_id"] == "it_cpu_b"
    assert cpu["resolved_price"] == 15000.0

    build = io.body("ready_builds", "b_dyn")
    assert build["price_total"] == 2800.0 + 15000.0
    assert build["slots_count"] == 2
    assert build.get("unresolved_count", 0) == 0


async def test_slot_qty_multiplies_line_and_build_total(io: FakeIO) -> None:
    svc = ReadyBuildsPipelineService(session=object())
    await _add_build(
        io,
        row_id="b_qty",
        name="С количеством",
        ram_gb=32,
        slots=[
            {"type_id": "etype_ram", "class_key": "ram_16", "mode": "dynamic", "qty": 2},
            {"type_id": "etype_cpu", "class_key": "cpu_6c", "mode": "dynamic", "qty": 1},
        ],
    )
    await svc.run(io)

    slots = {
        s["body"]["type_id"]: s["body"] for s in io._tables["ready_build_slots"]
        if s["body"]["build_id"] == "b_qty"
    }
    # 2 × 2800 (дешевейший в классе ram_16)
    assert slots["etype_ram"]["line_total"] == 5600.0
    assert slots["etype_cpu"]["line_total"] == 11000.0

    build = io.body("ready_builds", "b_qty")
    assert build["price_total"] == 16600.0
    # qty_total — штуки, slots_count — типы
    assert build["qty_total"] == 3
    assert build["slots_count"] == 2


async def test_class_signature_keeps_16gb_and_32gb_apart(io: FakeIO) -> None:
    """16 ГБ не «побеждает» 32 ГБ ценой: разные сигнатуры = разные витрины."""
    svc = ReadyBuildsPipelineService(session=object())
    # одинаковые слоты, но классовые атрибуты разные → разные сигнатуры
    await _add_build(
        io,
        row_id="b_16",
        name="16 ГБ (дешевле)",
        ram_gb=16,
        slots=[{"type_id": "etype_cpu", "class_key": "cpu_6c", "mode": "dynamic", "qty": 1}],
    )
    await _add_build(
        io,
        row_id="b_32",
        name="32 ГБ (дороже)",
        ram_gb=32,
        slots=[{"type_id": "etype_cpu", "class_key": "cpu_6c", "mode": "dynamic", "qty": 1}],
    )
    await svc.run(io)

    b16 = io.body("ready_builds", "b_16")
    b32 = io.body("ready_builds", "b_32")
    assert b16["class_signature"] != b32["class_signature"]
    assert b16["price_total"] == b32["price_total"] == 11000.0
    # обе «дешевлейшие» — каждая в своей витрине, альтернатив нет
    assert b16["is_cheapest_in_class"] is True
    assert b32["is_cheapest_in_class"] is True
    assert b16.get("alternatives_count", 0) == 0
    assert b32.get("alternatives_count", 0) == 0


async def test_same_class_competes_by_price(io: FakeIO) -> None:
    """Внутри одной сигнатуры дешевейшая побеждает, вторая — альтернатива."""
    svc = ReadyBuildsPipelineService(session=object())
    await _add_build(
        io,
        row_id="b_cheap",
        name="Дешевле",
        ram_gb=16,
        slots=[{"type_id": "etype_cpu", "class_key": "cpu_6c", "mode": "dynamic", "qty": 1}],
    )
    await _add_build(
        io,
        row_id="b_dear",
        name="Дороже",
        ram_gb=16,
        slots=[{"type_id": "etype_cpu", "class_key": "cpu_6c", "mode": "fixed",
                "item_id": "it_cpu_b", "qty": 1}],
    )
    await svc.run(io)

    cheap = io.body("ready_builds", "b_cheap")
    dear = io.body("ready_builds", "b_dear")
    assert cheap["class_signature"] == dear["class_signature"]
    assert cheap["price_total"] == 11000.0
    assert dear["price_total"] == 15000.0
    assert cheap["is_cheapest_in_class"] is True
    assert dear.get("is_cheapest_in_class") is not True
    assert dear.get("alternatives_count", 0) == 1


async def test_unresolved_slot_counted_and_on_order_flag(io: FakeIO) -> None:
    svc = ReadyBuildsPipelineService(session=object())
    await _add_build(
        io,
        row_id="b_ghost",
        name="С неразрешённым слотом",
        ram_gb=16,
        slots=[
            {"type_id": "etype_cpu", "class_key": "cpu_6c", "mode": "dynamic", "qty": 1},
            {"type_id": "etype_cooling", "class_key": "cool_1", "mode": "dynamic", "qty": 1},
        ],
    )
    await svc.run(io)

    build = io.body("ready_builds", "b_ghost")
    assert build["unresolved_count"] == 1
    assert build["price_total"] == 11000.0  # только разрешённый слот
    assert build.get("on_order") is not True

    stats = await svc.run(io)
    assert stats["unresolved_slots"] >= 1


async def test_group_aggregates(io: FakeIO) -> None:
    svc = ReadyBuildsPipelineService(session=object())
    await _add_build(
        io,
        row_id="b_a",
        name="A",
        ram_gb=16,
        slots=[{"type_id": "etype_cpu", "class_key": "cpu_6c", "mode": "dynamic", "qty": 1}],
    )
    await _add_build(
        io,
        row_id="b_b",
        name="B",
        ram_gb=16,
        slots=[{"type_id": "etype_cpu", "class_key": "cpu_6c", "mode": "fixed",
                "item_id": "it_cpu_b", "qty": 1}],
    )
    await svc.run(io)

    group = io.body("build_groups", "grp_am4")
    assert group["items_count"] == 6
    assert group["builds_count"] == 2
    assert group["price_min"] == 11000.0
    assert group["price_max"] == 15000.0
    assert group["synced_at"]


async def test_second_run_is_silent(io: FakeIO) -> None:
    """Guard от write-amplification: повторный прогон ничего не пишет."""
    svc = ReadyBuildsPipelineService(session=object())
    await _add_build(
        io,
        row_id="b_stable",
        name="Стабильная",
        ram_gb=16,
        slots=[{"type_id": "etype_cpu", "class_key": "cpu_6c", "mode": "dynamic", "qty": 1}],
    )
    await svc.run(io)
    io.updated.clear()

    stats = await svc.run(io)
    assert io.updated == []
    assert stats["items_updated"] == 0
    assert stats["slots_updated"] == 0
    assert stats["builds_updated"] == 0
    assert stats["groups_updated"] == 0


def test_class_signature_is_stable_and_separating() -> None:
    base = {"cpu_cores": 6, "ram_gb": 16, "storage_kind": "SSD", "storage_gb": 256}
    assert class_signature(base) == class_signature(dict(base))
    assert class_signature(base) != class_signature({**base, "ram_gb": 32})
    assert class_signature(base) != class_signature({**base, "storage_gb": 512})
    assert class_signature(base) != class_signature({**base, "storage_kind": "HDD"})
    assert class_signature(base) != class_signature({**base, "cpu_cores": 8})
    # «SSD 256» и «HDD 256» — разные витрины, а не одна
    assert class_signature({**base, "storage_kind": "HDD"}) != class_signature(base)


async def test_cascade_delete_catalog_group_and_build(io: FakeIO) -> None:
    """Каскад каталога: группа тянет пул и сборки со слотами; сборка — слоты."""
    from prodavan.application.modules.equipment_offers_service import (
        cascade_equipment_delete,
    )

    await _add_build(
        io,
        row_id="b_cas",
        name="Каскадная",
        ram_gb=16,
        slots=[{"type_id": "etype_cpu", "class_key": "cpu_6c", "mode": "dynamic", "qty": 1}],
    )
    slot_ids = [s["row_id"] for s in io._tables["ready_build_slots"]]
    assert slot_ids

    # удаление готовой сборки → её слоты
    await cascade_equipment_delete(
        object(),
        cabinet_id="cab",
        project_id=None,
        table_slug="ready_builds",
        row_id="b_cas",
        row_body={},
        principal=None,
        employee=None,
        session_id=None,
        io=io,
    )
    assert io._tables["ready_build_slots"] == []

    # удаление группы → пул и все её сборки
    await _add_build(
        io,
        row_id="b_group_cas",
        name="В группе",
        ram_gb=16,
        slots=[{"type_id": "etype_cpu", "class_key": "cpu_6c", "mode": "dynamic", "qty": 1}],
    )
    await cascade_equipment_delete(
        object(),
        cabinet_id="cab",
        project_id=None,
        table_slug="build_groups",
        row_id="grp_am4",
        row_body={},
        principal=None,
        employee=None,
        session_id=None,
        io=io,
    )
    assert io._tables["build_group_items"] == []
    assert io._tables["ready_builds"] == []
    assert io._tables["ready_build_slots"] == []


# Чат-таблицы нужны только тесту attach: каталог копируется в бакет чата.
def _chat_tables() -> dict[str, list[dict[str, Any]]]:
    return {
        "request_lines": [
            {"row_id": "line_1", "body": {"title": "Сборка ПК", "qty": 1, "status": "open"}}
        ],
        "equipment_builds": [],
        "found_groups": [],
        "found_offers": [],
        "budget_lines": [],
        "procurement": [],
        "equipment_items": [],
    }


async def test_attach_ready_build_copies_keys_into_chat(io: FakeIO, monkeypatch) -> None:
    """ready_build_attach копирует КЛЮЧИ (не цены) в чат: сборка + группы слотов.

    Копия живёт своей жизнью: материализация офферов, best, бюджет — штатная
    механика WAVE10, поэтому в копию должны попасть партномер/алиасы/хэш и qty.
    """
    import prodavan.application.modules.equipment_offers_service as eos
    from prodavan.application.modules.module_action_executor import ModuleActionExecutor
    from prodavan.domain.identity import Principal

    for table, rows in _chat_tables().items():
        io._tables.setdefault(table, rows)

    svc = ReadyBuildsPipelineService(session=object())
    await _add_build(
        io,
        row_id="b_src",
        name="Бюджетный ПК",
        ram_gb=16,
        slots=[
            {"type_id": "etype_cpu", "class_key": "cpu_6c", "mode": "dynamic", "qty": 1},
            {"type_id": "etype_ram", "class_key": "ram_16", "mode": "dynamic", "qty": 2},
        ],
    )
    await svc.run(io)

    # пайплайн чата проверен отдельными тестами — здесь фиксируем сам факт вызова
    pipeline_calls: list[bool] = []

    class _StubPipeline:
        def __init__(self, session):  # noqa: ANN001, D107
            pass

        async def run(self, io, *, materialize=True):  # noqa: ANN001, D103
            pipeline_calls.append(materialize)
            return {"offers_created": 0}

    monkeypatch.setattr(eos, "EquipmentPipelineService", _StubPipeline)
    monkeypatch.setattr(eos, "ModuleRowIO", lambda *a, **k: io)

    executor = ModuleActionExecutor(object())
    result = await executor._attach_ready_build(
        cabinet_id="cab",
        module_id="mod_equipment",
        params={"line_id": "line_1"},
        row_id="b_src",
        principal=Principal(sub="u1", roles=frozenset()),
        employee=None,
        project_id=None,
        session_id="chat_1",
    )

    assert result["ok"] is True
    assert result["line_id"] == "line_1"
    assert result["groups_created"] == 2
    assert result["slot_qty"] == {"etype_ram": 2}
    assert pipeline_calls == [True]  # копия сразу материализует офферы

    build_id = result["build_id"]
    chat_build = next(
        b["body"] for b in io._tables["equipment_builds"] if b["row_id"] == build_id
    )
    assert chat_build["source_ready_build_id"] == "b_src"
    assert chat_build["name"] == "Бюджетный ПК"
    assert chat_build["slot_qty"] == {"etype_ram": 2}

    copied = [
        g["body"] for g in io._tables["found_groups"]
        if g["body"].get("build_id") == build_id
    ]
    assert len(copied) == 2
    by_slot = {g["slot_type_id"]: g for g in copied}
    # ключи скопированы из РАЗРЕШЁННОГО компонента пула (дешевейшего в классе)
    assert by_slot["etype_cpu"]["part_number"] == "CPU-A"
    assert by_slot["etype_ram"]["part_number"] == "RAM-16B"
    assert by_slot["etype_cpu"]["match_kind"] == "exact"
    # владелец проставлен сразу: иначе до прогона пайплайна группы слотов
    # мелькали бы в общем списке «Найденные товары» (фильтр owner_kind = line)
    assert all(g["owner_kind"] == "build" for g in copied)
    # цены в копию не переносятся — их материализует пайплайн чата
    assert "best_price" not in by_slot["etype_cpu"]

    # id созданных групп возвращаются агенту: без них он не может работать с
    # копией прицельно (закрепить партномер слота, добавить алиас) —
    # found_groups_list фильтруется по build_id, а угадывать row_id слота
    # агент не должен
    assert {g["slot_type_id"] for g in result["groups"]} == {"etype_cpu", "etype_ram"}
    assert {g["group_id"] for g in result["groups"]} == {
        g["row_id"] for g in io._tables["found_groups"]
        if g["body"].get("build_id") == build_id
    }
    assert {g["slot_type_id"]: g["qty"] for g in result["groups"]} == {
        "etype_cpu": 1,
        "etype_ram": 2,
    }
    assert {g["slot_type_id"]: g["part_number"] for g in result["groups"]} == {
        "etype_cpu": "CPU-A",
        "etype_ram": "RAM-16B",
    }


async def test_attach_ready_build_requires_existing_line(io: FakeIO, monkeypatch) -> None:
    """Нельзя скопировать сборку на несуществующую позицию."""
    import prodavan.application.modules.equipment_offers_service as eos
    from prodavan.application.modules.module_action_executor import ModuleActionExecutor
    from prodavan.domain.errors import AppError
    from prodavan.domain.identity import Principal

    for table, rows in _chat_tables().items():
        io._tables.setdefault(table, rows)
    monkeypatch.setattr(eos, "ModuleRowIO", lambda *a, **k: io)

    executor = ModuleActionExecutor(object())
    with pytest.raises(AppError) as err:
        await executor._attach_ready_build(
            cabinet_id="cab",
            module_id="mod_equipment",
            params={"line_id": "line_missing"},
            row_id="b_src",
            principal=Principal(sub="u1", roles=frozenset()),
            employee=None,
            project_id=None,
            session_id="chat_1",
        )
    assert err.value.status == 404

    # без line_id — валидация 422
    with pytest.raises(AppError) as err2:
        await executor._attach_ready_build(
            cabinet_id="cab",
            module_id="mod_equipment",
            params={},
            row_id="b_src",
            principal=Principal(sub="u1", roles=frozenset()),
            employee=None,
            project_id=None,
            session_id="chat_1",
        )
    assert err2.value.status == 422
