"""Статические security/Supply-chain проверки Docker/template hello_plugin.

Тесты намеренно только читают файлы: они не строят образ и не разрешают
зависимости. Проходят в текущем прогоне.
"""
from __future__ import annotations

import re

from .conftest import (
    APP_PATH,
    BACKEND_DIR,
    DOCKERFILE_PATH,
    ENTRYPOINT_PATH,
    PLUGIN_ROOT,
    read_text,
)


def _instructions(raw: str) -> str:
    """Убрать comment-only строки, сохранив Docker/shell instructions."""
    return "\n".join(
        line for line in raw.splitlines() if not line.lstrip().startswith("#")
    )


def _logical_lines(raw: str) -> list[str]:
    """Склеить continuation lines lockfile без запуска pip."""
    result: list[str] = []
    pending = ""
    for source_line in raw.splitlines():
        line = source_line.strip()
        if line.endswith("\\"):
            pending += line[:-1] + " "
            continue
        result.append(pending + line)
        pending = ""
    if pending:
        result.append(pending)
    return [line for line in result if line]


def test_docker_has_no_mutable_syntax_or_tag_only_base() -> None:
    raw = read_text(DOCKERFILE_PATH)
    instructions = _instructions(raw)
    assert not re.search(r"(?im)^\s*#\s*syntax\s*=", raw)
    assert re.search(r"(?m)^ARG PYTHON_BASE_IMAGE$", instructions)
    assert not re.search(r"(?m)^ARG PYTHON_BASE_IMAGE=", instructions)
    assert "FROM ${PYTHON_BASE_IMAGE} AS python-base" in instructions
    assert not re.search(r"(?m)^FROM\s+python:", instructions)
    assert not re.search(r"(?im)^FROM\s+\S*:latest(?:\s|$)", instructions)
    # Проверка входного ARG — regex полного tag+digest, без fallback.
    assert "^python:3\\.13-slim@sha256:[0-9a-f]{64}$" in instructions
    assert "no tag-only or empty base is allowed" in instructions


def test_liquibase_requires_checksum_before_unpack_and_never_pipes_to_shell() -> None:
    raw = _instructions(read_text(DOCKERFILE_PATH))
    liquibase = raw.split("FROM python-base AS liquibase", 1)[1].split(
        "FROM python-base AS runtime", 1
    )[0]
    assert re.search(r"(?m)^ARG LIQUIBASE_VERSION=4\.24\.0$", liquibase)
    assert re.search(r"(?m)^ARG LIQUIBASE_SHA256$", liquibase)
    assert not re.search(r"(?m)^ARG LIQUIBASE_SHA256=", liquibase)
    assert "LIQUIBASE_VERSION}\" != '4.24.0'" in liquibase
    assert 'LIQUIBASE_SHA256}" /tmp/liquibase.tar.gz' in liquibase
    assert "sha256sum -c -" in liquibase
    assert liquibase.index("sha256sum -c -") < liquibase.index("tar -xzf")
    assert "v${LIQUIBASE_VERSION}/liquibase-${LIQUIBASE_VERSION}.tar.gz" in liquibase
    assert not re.search(
        r"\bcurl\b[^;]*\|\s*(?:/bin/)?(?:sh|bash)\b", raw
    ), "curl|sh/bash запрещён"


def test_runtime_is_nonroot_minimal_and_read_only_rootfs_compatible() -> None:
    raw = read_text(DOCKERFILE_PATH)
    runtime = _instructions(raw.split("FROM python-base AS runtime", 1)[1])
    assert re.search(r"(?m)^USER 10001:10001$", runtime)
    assert not re.search(r"(?m)^USER\s+(?:root|0)(?::0)?$", runtime)
    assert "HEALTHCHECK" in runtime
    assert "ENTRYPOINT" in runtime
    assert "default-jre-headless" in runtime
    assert "curl" not in runtime, "download tool не должен попадать в runtime"
    assert "chmod -R a-w /app /opt/liquibase /opt/venv" in runtime
    assert "HOME=/tmp" in runtime and "TMPDIR=/tmp" in runtime
    assert "readOnlyRootFilesystem=true" in raw
    assert "/tmp как" in raw and "emptyDir" in raw


def test_entrypoint_uses_env_for_dsn_and_migrations_precede_uvicorn() -> None:
    raw = read_text(ENTRYPOINT_PATH)
    assert "set -eu" in raw
    assert "export LIQUIBASE_COMMAND_URL" in raw
    assert "export LIQUIBASE_COMMAND_CHANGELOG_FILE" in raw
    assert "--url=" not in raw, "DSN с credentials не должен попадать в argv"
    assert "--changelog-file=" not in raw
    positions = (
        raw.index("export LIQUIBASE_COMMAND_URL"),
        raw.index("\nliquibase update\n"),
        raw.index("\nexport LIQUIBASE_UPDATE_OK=1\n"),
        raw.index("exec python -m uvicorn"),
    )
    assert list(positions) == sorted(positions)
    assert "LIQUIBASE_ALLOW_EXECUTE_COMMAND=false" in raw
    assert "--no-access-log" in raw


def test_docker_packages_runtime_api_and_app_mounts_it_once() -> None:
    docker = _instructions(read_text(DOCKERFILE_PATH))
    assert (
        "COPY backend/app.py backend/runtime_api.py backend/liquibase_url.py "
        "backend/entrypoint.sh ./"
    ) in docker
    assert docker.count("backend/runtime_api.py") == 1
    app = read_text(APP_PATH)
    assert app.count("install_runtime_api(") == 1
    assert "app.include_router" not in app


def test_dependencies_require_hashes_or_documented_explicit_blocker() -> None:
    docker = _instructions(read_text(DOCKERFILE_PATH))
    requirements = read_text(BACKEND_DIR / "requirements.txt")
    agents = read_text(PLUGIN_ROOT / "AGENTS.md")
    lock_path = BACKEND_DIR / "requirements.lock"

    assert "--require-hashes --no-deps -r requirements.lock" in docker
    assert "-r requirements.txt" not in docker
    assert "COPY backend/requirements* ./" in docker
    for direct in (
        "fastapi==0.115.6",
        "uvicorn[standard]==0.34.0",
        "asyncpg==0.30.0",
        "httpx==0.28.1",
    ):
        assert direct in requirements

    if lock_path.is_file():
        lock = read_text(lock_path)
        for line in _logical_lines(lock):
            if line.startswith("#") or line.startswith("--"):
                continue
            assert "==" in line, f"unpinned locked requirement: {line}"
            assert "--hash=sha256:" in line, f"unhashed locked requirement: {line}"
    else:
        # Отсутствие lock — разрешённый temporary state только с fail-closed guard.
        assert "if [ ! -f requirements.lock ]" in docker
        assert "backend/requirements.lock is required" in docker
        assert "exit 1" in docker
        assert "reviewed direct+transitive CPython 3.13 lock" in docker
        assert "verified inputs" in agents
        assert "Сейчас lock намеренно отсутствует" in agents


def test_no_static_plugin_claims_in_executable_template() -> None:
    for path in (APP_PATH, ENTRYPOINT_PATH):
        raw = read_text(path)
        assert "PLUGIN_CLAIMS" not in raw
        assert "PLUGIN_CLAIMS_RAW" not in raw
        assert "PLUGIN_CLAIMS_SECRET" not in raw
    docker = _instructions(read_text(DOCKERFILE_PATH))
    assert not re.search(r"(?m)^\s*(?:ARG|ENV)\s+PLUGIN_CLAIMS", docker)


def test_module_agents_marks_security_rules_and_unsupported_capabilities() -> None:
    raw = read_text(PLUGIN_ROOT / "AGENTS.md")
    for required in (
        "platform-owner review",
        "direct push в default branch",
        "Git tokens",
        "per-request introspection",
        "TENANT_DB_DSN",
        "Rollback кода НЕ равен rollback БД",
        "plugin-rpc/1",
        "rpc_operations",
        "hmac.compare_digest",
        "Content-Length",
        "supported",
        "target",
        "GitHub App",
        "requirements.lock",
        "LIQUIBASE_SHA256",
        "текущий fail-closed blocker",
    ):
        assert required in raw, f"AGENTS.md не фиксирует: {required}"
