#!/usr/bin/env python3
"""Validate cabinet pack JSON against schema (CI + local)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import jsonschema

REPO_ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATH = REPO_ROOT / "packages/schemas/cabinet-profile/v1.json"
PACK_DIR = REPO_ROOT / "packages/cabinet-packs/electronics-procurement"
PROFILE_PATH = PACK_DIR / "cabinet-profile.json"
PACK_JSON_PATH = PACK_DIR / "pack.json"

PACK_REQUIRED_KEYS = {"packId", "version", "profileFile", "capabilities"}


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def validate_pack_json(pack: dict) -> None:
    missing = PACK_REQUIRED_KEYS - pack.keys()
    if missing:
        raise ValueError(f"pack.json missing keys: {sorted(missing)}")


def main() -> int:
    schema = load_json(SCHEMA_PATH)
    profile = load_json(PROFILE_PATH)
    pack = load_json(PACK_JSON_PATH)

    validate_pack_json(pack)
    jsonschema.validate(profile, schema)

    print("OK: pack.json keys valid")
    print("OK: cabinet-profile.json validates against v1 schema")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
