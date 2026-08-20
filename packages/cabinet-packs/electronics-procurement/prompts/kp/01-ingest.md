# 01 — Ingest

Когда: новый файл в `inbox/` или «обработай эту спеку».

1. MCP `new_run('inbox/<файл>')` (или CLI `python tools/new_run.py --input inbox/<файл> --runs-dir runs`).
2. Скопированный оригинал лежит в `runs/<id>/input/`. Не править оригинал в inbox.
3. MCP `parse_spec(run_id)` / `python tools/parse_spec.py --run-id <id> --runs-dir runs`.
   - txt/csv/xlsx → `rows.json`.
   - xlsx: парсер **сам** отбрасывает `#REF!` / `#N/A` и **дедупит** одну позицию с разных листов (КП / Маржа / Спецификация). Не выкидывай 90 строк глазами.
   - Товарная позиция с P/N с рабочего листа важнее копии без P/N.
4. Странные, но товарные строки не молчи — оставь с пометкой. Пустые/служебные/Excel-ошибки уже отсечены.
5. Партномер копируй как есть. Кодировки: UTF-8 и Windows-1251.
6. Дальше — [02-classify.md](02-classify.md). Сразу после classify позиции в SQLite (`commerce.sqlite`).
