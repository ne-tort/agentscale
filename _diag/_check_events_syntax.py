"""Проверка синтаксиса (НЕ запуск тестов/линта/сборки) — структурная верификация."""

import ast
from pathlib import Path

root = Path(r"C:\Users\qwerty\git\agentscale\wt-migration")
files = [
    *sorted((root / "apps/api/src/prodavan/application/plugins/events").glob("*.py")),
    *sorted((root / "apps/api/tests/unit/plugins/events").glob("*.py")),
]
print(f"файлов: {len(files)}")
for p in files:
    src = p.read_text(encoding="utf-8")
    ast.parse(src, filename=str(p))
    print(f"OK  {p.relative_to(root)}  ({len(src.splitlines())} строк)")
