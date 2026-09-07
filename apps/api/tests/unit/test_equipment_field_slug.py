"""Unit tests for equipment field key slugify (mirrors Flutter slugifyFieldKey)."""

from __future__ import annotations

import re


def slugify_field_key(raw: str) -> str:
    """Python twin of Flutter slugifyFieldKey for seed/MCP key stability checks."""
    mapping = {
        "а": "a",
        "б": "b",
        "в": "v",
        "г": "g",
        "д": "d",
        "е": "e",
        "ё": "e",
        "ж": "zh",
        "з": "z",
        "и": "i",
        "й": "y",
        "к": "k",
        "л": "l",
        "м": "m",
        "н": "n",
        "о": "o",
        "п": "p",
        "р": "r",
        "с": "s",
        "т": "t",
        "у": "u",
        "ф": "f",
        "х": "h",
        "ц": "ts",
        "ч": "ch",
        "ш": "sh",
        "щ": "sch",
        "ъ": "",
        "ы": "y",
        "ь": "",
        "э": "e",
        "ю": "yu",
        "я": "ya",
    }
    out: list[str] = []
    for ch in raw.strip().lower():
        if ch in mapping:
            out.append(mapping[ch])
        elif re.match(r"[a-z0-9]", ch):
            out.append(ch)
        elif ch in " -/. ":
            out.append("_")
    key = re.sub(r"_+", "_", "".join(out)).strip("_")
    if not key:
        key = "field"
    if key[0].isdigit():
        key = f"f_{key}"
    return key[:48]


def test_slugify_cyrillic_characteristic() -> None:
    assert slugify_field_key("Количество ядер") == "kolichestvo_yader"


def test_slugify_latin_passthrough() -> None:
    assert slugify_field_key("Base Clock") == "base_clock"
