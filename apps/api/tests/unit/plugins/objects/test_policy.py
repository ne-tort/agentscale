"""Тесты генератора IAM-политик: нет ``agentscale/*``, session ⊂ parent,
никаких policy variables.

Покрывает: архитектура v4 §5.4 / §12.8 / §13.3, критерий 19 (часть про
политику), план миграции F3 «bucket-per-tenant + STS session policy».
"""

from __future__ import annotations

import json

import pytest

from prodavan.application.plugins.objects import (
    OBJECT_ACTIONS,
    POLICY_VERSION,
    bucket_name,
    bucket_policy,
    module_prefix,
    session_policy,
)
from prodavan.application.plugins.objects.errors import InvalidObjectKey


def _all_resources(policy: dict) -> list[str]:
    resources: list[str] = []
    for statement in policy["Statement"]:
        resources.extend(statement.get("Resource", []))
    return resources


def _all_actions(policy: dict) -> list[str]:
    actions: list[str] = []
    for statement in policy["Statement"]:
        actions.extend(statement.get("Action", []))
    return actions


def test_bucket_policy_never_mentions_legacy_agentscale_bucket() -> None:
    """As-built ``arn:aws:s3:::agentscale/*`` не должен просочиться в v4."""
    policy = bucket_policy("co_123", "mod_equipment")
    serialized = json.dumps(policy, ensure_ascii=False)
    assert "arn:aws:s3:::agentscale/*" not in serialized
    assert "arn:aws:s3:::agentscale" not in serialized
    assert "agentscale" not in serialized
    for resource in _all_resources(policy):
        assert resource.startswith("arn:aws:s3:::tenant-co_123")


def test_bucket_policy_resource_is_exact_tenant_bucket_and_module_prefix() -> None:
    """Требуемый ARN: ``arn:aws:s3:::tenant-<company>/module-<plugin>/*``."""
    policy = bucket_policy("co_123", "mod_equipment")
    resources = _all_resources(policy)
    assert "arn:aws:s3:::tenant-co_123" in resources
    assert "arn:aws:s3:::tenant-co_123/module-mod_equipment/*" in resources
    # Никаких '*' на весь бакет и никаких соседних компаний/плагинов.
    assert "arn:aws:s3:::tenant-co_123/*" not in resources
    assert all("tenant-other" not in r for r in resources)
    assert policy["Version"] == POLICY_VERSION


def test_bucket_policy_allows_only_four_actions() -> None:
    """Только GetObject/PutObject/DeleteObject/ListBucket, без s3:* и admin."""
    actions = _all_actions(bucket_policy("co_123", "mod_equipment"))
    assert set(actions) == set(OBJECT_ACTIONS)
    assert "s3:*" not in actions
    for forbidden in (
        "s3:CreateBucket",
        "s3:DeleteBucket",
        "s3:PutBucketPolicy",
        "s3:GetBucketLocation",
        "s3:DeleteObjectVersion",
    ):
        assert forbidden not in actions


def test_bucket_policy_list_bucket_has_prefix_condition() -> None:
    """ListBucket без Condition перечислил бы соседние плагины компании."""
    policy = bucket_policy("co_123", "mod_equipment")
    list_statements = [s for s in policy["Statement"] if "s3:ListBucket" in s["Action"]]
    assert len(list_statements) == 1
    for statement in list_statements:
        assert statement["Effect"] == "Allow"
        condition = statement.get("Condition")
        assert condition is not None, "Condition на префикс обязателен"
        assert condition["StringLike"]["s3:prefix"] == ["module-mod_equipment/*"]
    object_stmt = next(s for s in policy["Statement"] if "s3:GetObject" in s["Action"])
    assert "Condition" not in object_stmt
    assert object_stmt["Resource"] == ["arn:aws:s3:::tenant-co_123/module-mod_equipment/*"]


def test_condition_keys_match_minio_actions_without_widening_scope() -> None:
    """MinIO принимает s3:prefix для ListBucket, не для object actions."""
    condition_actions = {"s3:prefix": frozenset({"s3:ListBucket"})}
    expected_resources = [
        "arn:aws:s3:::tenant-co_123",
        "arn:aws:s3:::tenant-co_123/module-mod_equipment/*",
    ]
    for policy in (
        bucket_policy("co_123", "mod_equipment"),
        session_policy("co_123", "mod_equipment"),
    ):
        assert _all_resources(policy) == expected_resources
        for statement in policy["Statement"]:
            actions = frozenset(statement["Action"])
            for condition in statement.get("Condition", {}).values():
                for key, values in condition.items():
                    assert key in condition_actions
                    assert actions
                    assert actions <= condition_actions[key]
                    assert values == ["module-mod_equipment/*"]
        object_stmt = next(s for s in policy["Statement"] if "s3:GetObject" in s["Action"])
        assert "Condition" not in object_stmt
        assert object_stmt["Resource"] == [expected_resources[1]]


def test_session_policy_is_subset_of_parent() -> None:
    """STS ``AssumeRole`` не может расширить права родительской роли."""
    parent = bucket_policy("co_123", "mod_equipment")
    session = session_policy("co_123", "mod_equipment")
    assert session["Version"] == parent["Version"]

    def canon(statements: list[dict]) -> set[str]:
        return {json.dumps(s, sort_keys=True, separators=(",", ":")) for s in statements}

    assert canon(session["Statement"]) <= canon(parent["Statement"])
    assert set(_all_actions(session)) == set(OBJECT_ACTIONS)
    assert _all_resources(session) == _all_resources(parent)


def test_session_policy_is_scoped_to_its_own_pair_only() -> None:
    """Политика пары (co_a, mod_x) не даёт доступ к (co_b, mod_y)."""
    policy = session_policy("co_a", "mod_x")
    serialized = json.dumps(policy)
    assert "tenant-co_a" in serialized
    assert "module-mod_x" in serialized
    assert "tenant-co_b" not in serialized
    assert "module-mod_y" not in serialized


def test_policies_use_no_policy_variables() -> None:
    """По умолчанию НЕ используем ``${aws:username}`` / ``${jwt:*}``.

    Это опция MinIO, требующая подтверждения официальной документацией
    (v4 §12.8, блокер плана §5.1). Динамизм достигается явной генерацией
    политики под пару (company, plugin) на доверенной стороне.
    """
    for policy in (
        bucket_policy("co_123", "mod_equipment"),
        session_policy("co_123", "mod_equipment"),
    ):
        serialized = json.dumps(policy)
        assert "${aws:username}" not in serialized
        assert "${jwt:" not in serialized
        assert "${" not in serialized


def test_bucket_and_prefix_are_deterministic_and_validated() -> None:
    assert bucket_name("co_123") == "tenant-co_123"
    assert module_prefix("mod_equipment") == "module-mod_equipment/"
    # Имя бакета/префикса строится из провалидированного идентификатора:
    # обход пути, '*' и ARN-инъекции невозможны (fail-closed).
    for bad in ("", "../other", "co/*", "co/../../x", "a:b", "co name", "co\n", "CO_123"):
        with pytest.raises(InvalidObjectKey):
            bucket_name(bad)
        with pytest.raises(InvalidObjectKey):
            module_prefix(bad)
        with pytest.raises(InvalidObjectKey):
            bucket_policy(bad, "mod_equipment")
        with pytest.raises(InvalidObjectKey):
            bucket_policy("co_123", bad)


def test_uppercase_company_id_is_rejected_not_silently_lowercased() -> None:
    """«Привести к lower» объединило бы две компании в одном бакете."""
    with pytest.raises(InvalidObjectKey):
        bucket_name("CO_123")
    assert bucket_name("co_123") == "tenant-co_123"


def test_policy_json_is_serializable_for_assume_role() -> None:
    """Session policy уходит в ``Policy=json.dumps(...)`` — без кастомных типов."""
    dumped = json.dumps(session_policy("co_123", "mod_equipment"))
    assert json.loads(dumped)["Version"] == POLICY_VERSION
