"""Исчерпание retry при конфликтах: ``K8S_CONFLICT`` и его типизация.

Существующий набор проверял УСПЕШНЫЙ ретрай после единственного 409 и подтип
изменившегося digest/owner, но ни разу не исчерпывал попытки. Именно поэтому
не был закрыт вопрос: что происходит, если конфликт повторяется на каждой
попытке — бесконечный цикл, тихая перезапись или типизированный отказ.

Пути и методы взяты из фактической трассы ensure, поведение — из
``_apply_attempts``.
"""

from __future__ import annotations

import pytest

from prodavan.infrastructure.plugins.runtime.deployment_admin import (
    DeploymentConflictError,
    DeploymentUnavailableError,
)

from .test_deployment_admin import NAMESPACE, _harness, _kwargs

pytestmark = pytest.mark.asyncio


async def test_exhausted_create_conflicts_raise_a_typed_retryable_error() -> None:
    admin, api, _, _, _, _ = _harness()
    # Больше конфликтов, чем попыток: ретраи обязаны закончиться отказом, а не
    # бесконечным циклом и не тихой перезаписью чужого объекта.
    api.conflicts[("POST", f"/api/v1/namespaces/{NAMESPACE}/services")] = 99
    with pytest.raises(DeploymentConflictError) as caught:
        await admin.ensure_deployment(**_kwargs())
    assert caught.value.code == "K8S_CONFLICT"
    assert caught.value.retryable is True


async def test_conflict_is_not_downgraded_to_cluster_unavailable() -> None:
    admin, api, _, _, _, _ = _harness()
    api.conflicts[("POST", f"/api/v1/namespaces/{NAMESPACE}/services")] = 99
    with pytest.raises(DeploymentConflictError):
        await admin.ensure_deployment(**_kwargs())
    # Вызывающий должен отличать 409 (повторить операцию) от недоступности
    # кластера: у них разные коды и разные ветки обработки.
    assert not issubclass(DeploymentConflictError, DeploymentUnavailableError)


async def test_retries_are_bounded_and_do_not_loop_forever() -> None:
    admin, api, _, _, _, _ = _harness()
    api.conflicts[("POST", f"/api/v1/namespaces/{NAMESPACE}/services")] = 99
    with pytest.raises(DeploymentConflictError):
        await admin.ensure_deployment(**_kwargs())
    # Цикл ограничен _apply_attempts: ровно столько POST'ов, не больше.
    posts = [c for c in api.calls if c["method"] == "POST" and c["path"].endswith("/services")]
    assert len(posts) == admin._apply_attempts
