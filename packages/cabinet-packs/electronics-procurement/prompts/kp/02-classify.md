# 02 — Classify

Вход: `rows.json`. Выход: `lineitems.json` (+ хвост в `needs-review.json`) **и сразу** позиции в `commerce.sqlite` (MCP `classify_rows` / `offers_upsert_lineitem`).

Схема: [`schemas/lineitem.schema.json`](../schemas/lineitem.schema.json).  
Идентификация и карточки: [`07-equipment.md`](07-equipment.md).

Источник правды позиций — **SQLite проекта**, не «я оставил 8 строк с листа Маржа в уме». Дубли одной номенклатуры с разных листов — одна `lineitem` (тот же P/N + qty). `#REF!` в xlsx — не товар.

Для каждого ряда:

- `raw_text` — дословно.
- `category`: `mouse | monitor | psu | notebook | desktop | ram | cpu | gpu | motherboard | storage | network | server | other | unknown`.
- **`part_number` только если явно виден** (отдельная ячейка, `{PN}`, `P/N`, «арт.»). Описание «Ноутбук ACER 16 ГБ» **без** артикула → `part_number=null`. Это другой класс поиска, не тот же что «Ноутбук ACER 16 ГБ NX.xxxx».
- `constraints` — нормализованные требования (`ram_gb=16`, `min_ports=16`, `panel=IPS`). Не записывай в P/N то, что является характеристикой.
- `bundle_hint=true` если сборка ПК/сервера, не один SKU.
- `confidence` 0..1. Ниже ~0.6 или `unknown` → `needs_review: true`.

Типы неопределённости (не путать):

1. Есть P/N — идентифицировано; дальше exact.
2. Бренд + часть характеристик, **нет** P/N — частично; веб на линейку.
3. Только роль («мышь») — не подставляй любимый SKU.
4. Метафора («хреновина с 16 портами») — гипотеза категории. См. [03-clarify.md](03-clarify.md). **Иди в интернет**, не угадывай.
5. Сборка («системный блок i5», «память в сервер Dell») — `bundle_hint`, не комплектуй молча. Совместимость — [07-equipment.md](07-equipment.md).

Синонимы: мышка=mouse, оперативка=ram, винт/ssd=storage, видеокарта=gpu, материнка=motherboard, блок питания ноутбука ≠ ATX PSU.

`classify_rows` даёт **черновик** (`category: unknown`). Уточни категорию/constraints по схеме выше и вызови **`save_lineitems`** — не оставляй stub для search.
