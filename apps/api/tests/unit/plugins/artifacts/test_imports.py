"""Проверка консистентности исходников зоны (без pytest-импорта prodavan).

Используется ТОЛЬКО статический анализ текста: тесты как таковые не запускаются
(verification freeze), этот модуль — документация намерений + ast.parse.
"""

from __future__ import annotations

import ast
from pathlib import Path

SRC = Path(__file__).resolve().parents[4] / "src" / "prodavan" / "application" / "plugins" / "artifacts"
TESTS = Path(__file__).resolve().parent


def _sources() -> list[Path]:
    return sorted([*SRC.glob("*.py"), *TESTS.glob("*.py")])


def test_all_modules_parse() -> None:
    """Все .py зоны разбираются ast.parse; компиляция/линт НЕ запускаются."""
    files = _sources()
    assert files, "ожидались файлы зоны artifacts"
    for path in files:
        ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def test_expected_files_exist() -> None:
    names = {path.name for path in SRC.glob("*.py")}
    assert {
        "__init__.py",
        "errors.py",
        "oci.py",
        "verification.py",
        "registry.py",
        "layers.py",
    } <= names
