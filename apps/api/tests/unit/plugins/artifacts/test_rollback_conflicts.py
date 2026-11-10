"""Rollback-конфликты реестра артефактов, которые не проверялись.

Существующий набор покрывал только mismatch по ``reason``/``actor``. Ветки
«цель уже current», «replay source исчез», «generation конфликт» и «current
version конфликт» не вызывались ни одним тестом — то есть регрессия, при
которой повторный rollback молча перезаписывает текущий артефакт, проходила
бы незамеченной.

Тесты используют те же фикстуры/хелперы, что и основной модуль реестра.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from prodavan.application.dynamic_app_registry.contracts import (
    RollbackCommand,
    RollbackConflictError,
)

from .conftest import DIGEST_A, DIGEST_B, ref, valid_manifest
from .test_registry import _publish, _verified

pytestmark = pytest.mark.asyncio


async def _two_versions(registry, verifier, store):
    manifest_v1 = valid_manifest(version="1.1.0")
    manifest_v2 = valid_manifest(version="1.2.0")
    await _publish(registry, ref(DIGEST_B), manifest_v1, _verified(verifier, manifest_v1, DIGEST_B))
    await _publish(registry, ref(DIGEST_A), manifest_v2, _verified(verifier, manifest_v2, DIGEST_A))
    return store.by_digest(DIGEST_B), store.by_digest(DIGEST_A)


async def test_rollback_target_already_current_is_a_conflict(registry, verifier, store) -> None:
    target, _ = await _two_versions(registry, verifier, store)
    # Цель = 1.1.0, сначала откатываемся на неё.
    command = RollbackCommand(
        definition_id="mod_equipment",
        target_version_id="1.1.0",
        actor="admin",
        reason="first",
        expected_current_version_id="1.2.0",
        expected_artifact_digest=DIGEST_B,
        expected_current_lifecycle_version=1,
    )
    await store.rollback_async(
        target=target, command=command, actor="admin", detail="first",
    )
    before = store.lifecycle_log
    # Повторный rollback БЕЗ expected_source: цель уже current -> конфликт.
    replay = replace(command, expected_current_version_id=None)
    with pytest.raises(RollbackConflictError, match="already current"):
        await store.rollback_async(
            target=target, command=replay, actor="admin", detail="first",
        )
    assert store.lifecycle_log == before


async def test_rollback_current_version_conflict_leaves_state_untouched(registry, verifier, store) -> None:
    target, _ = await _two_versions(registry, verifier, store)
    before = store.lifecycle_log
    command = RollbackCommand(
        definition_id="mod_equipment",
        target_version_id="1.1.0",
        actor="admin",
        reason="stale",
        # Ожидаем другую текущую версию, чем реальная.
        expected_current_version_id="1.3.0",
        expected_artifact_digest=DIGEST_B,
        expected_current_lifecycle_version=1,
    )
    with pytest.raises(RollbackConflictError, match="current version conflict"):
        await store.rollback_async(
            target=target, command=command, actor="admin", detail="stale",
        )
    # Ничего не переключилось и не записалось в журнал жизненного цикла.
    assert store.lifecycle_log == before
    assert (await registry.current_async("mod_equipment")).digest == DIGEST_A


async def test_rollback_replay_source_missing_is_a_conflict(registry, verifier, store) -> None:
    target, _ = await _two_versions(registry, verifier, store)
    command = RollbackCommand(
        definition_id="mod_equipment",
        target_version_id="1.1.0",
        actor="admin",
        reason="first",
        expected_current_version_id="1.2.0",
        expected_artifact_digest=DIGEST_B,
        expected_current_lifecycle_version=1,
    )
    await store.rollback_async(
        target=target, command=command, actor="admin", detail="first",
    )
    # Повтор с несуществующим source: replay не имеет evidence.
    replay = replace(command, expected_current_version_id="9.9.9")
    with pytest.raises(RollbackConflictError):
        await store.rollback_async(
            target=target, command=replay, actor="admin", detail="first",
        )
