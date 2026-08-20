# 07 — Идентификация, характеристики, аналоги, сборки

Модуль для **уточнения что это за техника**, **карточек характеристик**, **аналогов** и **совместимых сборок**.  
MCP: `commerce-equipment` (`equipment_upsert` / `equipment_list`). Веб-магазины: `list_web_shops`, бот `/магазины`.

Цены в карточку **не пиши** (цены только `offers.json`). Характеристики — да, со ссылкой в `sources`.

## Не экономить на интернете

Ходи в веб **часто**: неясный идентификатор, сверка P/N, семейство модели, аналоги, совместимость RAM/CPU/MB, TDP/сокет, «а это вообще серверная планка?».  
«Я и так знаю» — недостаточно: перепроверь даташит/страницу линейки/витрину из allowlist.

Сайты: `list_web_shops()` — `kind=shop` (DNS, Citilink, Ozon, AliExpress, …) и `kind=specs` (ixbt и т.п.). Не уходи на случайный первый магазин вне списка, пока список не пуст.

## Два класса позиций (не путать)

| В спеке | Пример | Круг поиска |
| --- | --- | --- |
| **Есть P/N** (отдельное поле, `{PN}`, `P/N`, артикул) | Ноутбук ACER 16 ГБ **NX.KRTCD.002** | **~90% exact этот P/N**. Редко — то же семейство той же фирмы, если exact нет. Аналоги по характеристикам — только если нет ни exact, ни семейства. |
| **Нет P/N**, только описание | Ноутбук ACER 16 ГБ | Категория + бренд + **обязательные** constraints (RAM, диагональ, CPU class). Не подставляй конкретный SKU «потому что Acer». Сначала веб на линейку, потом офферы. |

Не «улучшай» и не нормализуй P/N заказчика (не NX.KRTCD.002 → Aspire 5).

## Порядок идентификации

1. Classify: `part_number` только если явно виден; иначе `null`.
2. Если идентификатор мусорный / неизвестная аббревиатура — веб **что это**, не покупка.
3. Собрать `attrs` по форме категории (ниже) + `notes` свободным текстом.
4. `equipment_upsert(project, card_json)` в **текущий проект**.
5. Потом search/rank. Analog: только после честных характеристик.

## Совместимость и сборки

Если `bundle_hint` / «собери ПК» / «сервер + память / диск / RAID»:

- Ты эксперт по совместимости, но **перепроверяй** (сокет, DDR4 vs DDR5, ECC vs non-ECC, RDIMM vs UDIMM, мощность PSU, PCIe поколение, форм-фактор диска, backplane).
- Не комплектуй молча без источников. Запиши в `compatibility[]` факты («DDR5-5600 SODIMM, не ECC»).
- Для серверов: поколение CPU, chipset, max RAM, supported DIMM type — с витрины/даташита, не из памяти модели.

## Формы `attrs` (унификация между проектами)

Ключи латиницей snake_case. Чего нет — не выдумывай, оставь в `notes`.

### notebook
`cpu`, `ram_gb`, `ram_type` (DDR4/DDR5), `storage_gb`, `storage_type` (SSD/HDD/NVMe), `screen_inch`, `resolution`, `gpu`, `os`, `weight_kg`

### desktop / сборка ПК
`cpu`, `socket`, `motherboard_chipset`, `ram_gb`, `ram_type`, `gpu`, `storage_gb`, `psu_watt`, `form_factor` (mATX/ATX), `case`

### cpu
`socket`, `cores`, `threads`, `tdp_w`, `base_ghz`, `igpu` (bool/text)

### ram
`ddr` (4/5), `speed_mhz`, `size_gb`, `ecc` (bool), `registered` (RDIMM/UDIMM/SODIMM), `rank`

### storage
`kind` (ssd/hdd/nvme), `interface` (SATA/U.2/M.2), `form_factor` (2.5/3.5/2280), `capacity_gb`, `endurance`

### gpu
`interface` (PCIe 4.0 x16), `length_mm`, `tdp_w`, `power_connectors`, `vram_gb`

### motherboard
`socket`, `chipset`, `form_factor`, `ram_type`, `ram_slots`, `max_ram_gb`, `m2_slots`

### psu
`watt`, `80plus`, `modular`, `form_factor`

### monitor
`diagonal_inch`, `panel`, `resolution`, `hz`, `ports`

### network
`ports`, `speed_gbps`, `poe`, `managed`, `sfp`

### server
`cpu_gen`, `socket`, `ram_type`, `max_ram_gb`, `bays`, `raid`, `psu_watt`, `form_factor` (1U/2U/tower)

### mouse / other
минимум: `interface`, `sensor` / свободные ключи + `notes`

## MCP `commerce-equipment` vs `specs_*` в offers

Одна таблица характеристик в `commerce.sqlite`:

| Фаза | MCP | Тул |
| --- | --- | --- |
| identify / clarify / search | `commerce-equipment` | `equipment_upsert`, `equipment_list` |
| output / связь с вариантами | `commerce-offers` | `specs_upsert`, `specs_link`, `specs_for_variant` |

Не дублируй карточку: если уже `equipment_upsert` — на output только `specs_link` при необходимости.

- `project` опционален — из `[Проект: …]` / `COMMERCE_PROJECT`.
- `equipment_upsert` после каждой уверенной идентификации.
- `equipment_list` / `equipment_get` перед аналогом — не дублируй карточку.
- Оператор: Telegram `/характеристики`.
