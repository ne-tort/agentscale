"""Shared denylist / path rules for workspace dehydrate (pod→object store)."""

from __future__ import annotations

from pathlib import PurePosixPath

# Top-level or nested junk that must not round-trip into MinIO last-good.
_EXCLUDED_NAME_PARTS = frozenset(
    {
        "node_modules",
        ".git",
        "__pycache__",
        ".venv",
        "venv",
        ".tox",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        # Agent runtime crash-buffer / local session files — SoT is Postgres.
        # Round-tripping root-owned hydrate of these causes EACCES in agent-runtime.
        ".openclaw-data",
    }
)

_MAX_FILE_BYTES = 32 * 1024 * 1024  # 32 MiB per file safety cap

# Платформенные артефакты: источник истины — Postgres (file_ref / seed-zip),
# а workspace пода — лишь производная копия, которую материализация перезаписывает.
# Выкачивать их из пода нельзя: копия пода может быть только равной или УСТАРЕВШЕЙ,
# поэтому дегидратация затирала свежий MCP-пакет старым (обновление версии пакета
# не доезжало до существующих проектов — агент не видел новых инструментов).
#
# `.prodavan/config.yaml` и `AGENTS.md` добавлены по той же причине: чекапоинт
# старого пода перезатирал свежий конфиг провайдера, и пересозданный под
# гидрировался СТАРЫМ base_url — чат показывал модели нового провайдера, а вызов
# падал в 401 «Invalid token». Полный набор материализованных путей динамический
# (`{{target_path}}` в правилах модулей), поэтому дополнительно читается манифест
# — см. `materialized_paths_from_manifest`.
_PLATFORM_OWNED_TOP_LEVEL = frozenset({"mcp.json", "AGENTS.md"})
_PLATFORM_OWNED_DIRS = frozenset({"packages", ".prodavan"})


def materialized_paths_from_manifest(manifest: dict | None) -> frozenset[str]:
    """Пути из `projects.materialize_manifest` ({module_id: [workspace_path, …]}).

    Манифест — единственная полная правда о том, что пишет материализация:
    пути модулей шаблонные (`{{target_path}}`), статическим списком их не покрыть.
    """
    out: set[str] = set()
    if not isinstance(manifest, dict):
        return frozenset()
    for paths in manifest.values():
        if not isinstance(paths, (list, tuple, set)):
            continue
        for raw in paths:
            rel = str(raw or "").replace("\\", "/").strip().strip("/")
            # "." или "" объявили бы платформенным вообще всё — включая файлы агента
            if not rel or rel in {".", ".."} or rel.startswith("../"):
                continue
            out.add(rel)
    return frozenset(out)


def is_platform_owned_rel(
    rel: str, *, materialized: frozenset[str] = frozenset()
) -> bool:
    """True для путей, которые принадлежат платформе, а не пользователю/агенту."""
    path = PurePosixPath(rel.replace("\\", "/").lstrip("/"))
    if not path.parts or path.parts[0] == "..":
        return False
    if len(path.parts) == 1 and path.parts[0] in _PLATFORM_OWNED_TOP_LEVEL:
        return True
    if path.parts[0] in _PLATFORM_OWNED_DIRS:
        return True
    if materialized:
        normalized = path.as_posix()
        for owned in materialized:
            # запись манифеста может быть как файлом, так и каталогом
            # (например `packages/prodavan-equipment`)
            if normalized == owned or normalized.startswith(f"{owned}/"):
                return True
    return False


def is_excluded_rel(rel: str) -> bool:
    """Return True if relative path under /workspace should not be uploaded."""
    path = PurePosixPath(rel.replace("\\", "/").lstrip("/"))
    if not path.parts or path.parts[0] == "..":
        return True
    for part in path.parts:
        if part in _EXCLUDED_NAME_PARTS:
            return True
        if part.endswith(".pyc"):
            return True
    return False


def tar_exclude_args() -> list[str]:
    """GNU/busybox tar --exclude args for dehydrate stream."""
    args: list[str] = []
    for name in sorted(_EXCLUDED_NAME_PARTS):
        args.extend(["--exclude", f"./{name}"])
        args.extend(["--exclude", f"*/{name}"])
        args.extend(["--exclude", f"*/{name}/*"])
    args.extend(["--exclude", "*.pyc"])
    return args


def max_file_bytes() -> int:
    return _MAX_FILE_BYTES
