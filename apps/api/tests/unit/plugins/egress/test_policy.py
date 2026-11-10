"""Unit-тесты egress-политики (v4 §6.3, критерии 8/22).

Тесты проходят в текущем прогоне (`pytest tests/unit/plugins`).
"""

from __future__ import annotations

import pytest

from prodavan.application.plugins.egress.errors import EgressDenied, EgressPolicyError
from prodavan.application.plugins.egress.policy import (
    EgressMode,
    EgressPolicy,
    EgressRule,
    match,
    normalize_path,
    resolve_policy,
    validate_host,
)


def _grant(**egress: object) -> dict[str, object]:
    """Минимальный ``CapabilityGrant`` в БД (dict, не токен)."""
    return {"grant_id": "grant_1", "egress": dict(egress)}


# --------------------------------------------------------------------- none


def test_none_mode_denies_everything() -> None:
    """Критерий 8 / §6.3: ``none`` — только gateway + DNS, правил egress нет."""
    policy = resolve_policy(_grant(mode="none"))
    assert policy.mode is EgressMode.NONE
    assert policy.rules == ()
    assert policy.allowed_hosts == ()
    assert policy.allows_any is False
    # Даже легальный хост не совпадает ни с одним правилом.
    assert not any(match(rule, "hooks.slack.com", "/services/xxx", "POST") for rule in policy.rules)


def test_none_mode_ignores_accidental_allowlist() -> None:
    """Ошибочно лежащий allowlist не должен «оживить» режим ``none``."""
    policy = resolve_policy(
        _grant(mode="none", allowed_hosts=["hooks.slack.com"], allowed_paths=["/services"])
    )
    assert policy.rules == ()
    assert policy.allowed_hosts == ()


def test_missing_egress_section_is_none() -> None:
    """Отсутствие секции egress = выключенный egress (fail-closed)."""
    policy = resolve_policy({"grant_id": "grant_1"})
    assert policy.mode is EgressMode.NONE
    assert policy.allows_any is False


# ------------------------------------------------------------ webhooks-only


def test_webhooks_only_allows_only_declared_path() -> None:
    """``hooks.slack.com/services/*`` пускает ТОЛЬКО этот путь."""
    policy = resolve_policy(
        _grant(
            mode="webhooks-only",
            allowed_hosts=["hooks.slack.com"],
            allowed_paths=["/services"],
            max_egress_bytes=1024,
        )
    )
    rule = EgressRule("hooks.slack.com", "/services", "POST")
    assert match(rule, "hooks.slack.com", "/services/T000/B000/XXXX", "POST") is True
    assert match(rule, "hooks.slack.com", "/services", "POST") is True
    # Чужой путь, «похожий» префикс и чужой метод — отказ.
    assert match(rule, "hooks.slack.com", "/api/chat.postMessage", "POST") is False
    assert match(rule, "hooks.slack.com", "/servicesevil", "POST") is False
    assert match(rule, "hooks.slack.com", "/services/xxx", "GET") is False
    assert match(rule, "hooks.slack.com.evil.com", "/services/xxx", "POST") is False


def test_webhooks_only_requires_non_empty_allowlist() -> None:
    """Пустой allowlist при ``webhooks-only`` = отказ, а не «разрешить всё»."""
    with pytest.raises(EgressDenied) as ei:
        resolve_policy(_grant(mode="webhooks-only", allowed_hosts=[]))
    assert ei.value.reason == "allowlist_empty"


def test_host_evil_com_with_slack_allowlist_is_denied() -> None:
    """Критерий 8: подмена ``Host: evil.com`` при allowlist slack -> 403."""
    policy = resolve_policy(
        _grant(
            mode="webhooks-only",
            allowed_hosts=["hooks.slack.com"],
            allowed_paths=["/services"],
        )
    )
    assert not any(match(rule, "evil.com", "/services/xxx", "POST") for rule in policy.rules)
    assert not any(match(rule, "evil.com", "/", "GET") for rule in policy.rules)
    # И наоборот: « slack-похожий» хост не проходит точное сравнение.
    assert not any(match(rule, "hooks.slack.com.attacker.net", "/services/x", "POST") for rule in policy.rules)


def test_webhooks_only_rejects_wildcard_host() -> None:
    """Wildcard `*` в host при webhooks-only запрещён (§6.3)."""
    with pytest.raises(EgressPolicyError) as ei:
        resolve_policy(_grant(mode="webhooks-only", allowed_hosts=["*"], allowed_paths=["/"]))
    assert ei.value.reason == "host_wildcard"
    with pytest.raises(EgressDenied):
        resolve_policy(_grant(mode="webhooks-only", allowed_hosts=["*.slack.com"], allowed_paths=["/"]))


# ----------------------------------------------------------- SSRF / приватные адреса


@pytest.mark.parametrize(
    "host",
    [
        "127.0.0.1",
        "10.0.0.5",
        "192.168.1.1",
        "172.16.0.1",
        "169.254.169.254",
        "169.254.170.2",
        "0.0.0.0",
        "::1",
        "2130706433",  # decimal-форма 127.0.0.1
        "0177.0.0.1",  # octal-форма loopback
        "0x7f.0.0.1",  # hex-форма loopback
        "127.1",  # короткая форма IPv4
        "metadata.google.internal",
        "localhost",
        "minio.internal",
        "tenant-postgres",  # одноуровневое k8s-имя
        "8.8.8.8",  # публичный IP-литерал тоже запрещён (обход по IP)
    ],
)
def test_private_loopback_metadata_hosts_are_rejected(host: str) -> None:
    """SSRF: частные IP / loopback / metadata -> отказ (§6.3)."""
    with pytest.raises(EgressDenied):
        validate_host(host)
    with pytest.raises(EgressDenied):
        resolve_policy(_grant(mode="webhooks-only", allowed_hosts=[host], allowed_paths=["/"]))


@pytest.mark.parametrize(
    "host",
    [
        "http://hooks.slack.com",
        "hooks.slack.com:443",
        "user@hooks.slack.com",
        "hooks.slack.com/evil",
        "hooks_slack_com",
        "-hooks.slack.com",
        "hooks-.slack.com",
        "hooks..slack.com",
        "hooks.slack.com ",
        "",
    ],
)
def test_non_dns_host_strings_are_rejected(host: str) -> None:
    """Защита от подмены Host: схема, порт, credentials, underscore — отказ."""
    with pytest.raises(EgressDenied):
        validate_host(host)


def test_valid_public_dns_host_is_normalized() -> None:
    assert validate_host("Hooks.Slack.COM.") == "hooks.slack.com"
    assert validate_host("api.github.com") == "api.github.com"


# --------------------------------------------------------------------- full


def test_full_mode_without_explicit_approved_is_denied() -> None:
    """``full`` без явного ``approved`` -> отказ (§6.3)."""
    with pytest.raises(EgressDenied) as ei:
        resolve_policy(
            _grant(mode="full", allowed_hosts=["api.github.com"], allowed_paths=["/"])
        )
    assert ei.value.reason == "approval_missing"


@pytest.mark.parametrize("fake_approval", ["true", 1, [True], None, False])
def test_full_mode_rejects_non_boolean_approval(fake_approval: object) -> None:
    """Строка/число/список не считаются approval'ом."""
    with pytest.raises(EgressDenied):
        resolve_policy(
            _grant(
                mode="full",
                approved=fake_approval,
                allowed_hosts=["api.github.com"],
                allowed_paths=["/"],
            )
        )


def test_full_mode_with_explicit_approved_and_quota() -> None:
    """Явный ``approved: true`` + квота байт -> политика собирается."""
    policy = resolve_policy(
        _grant(
            mode="full",
            approved=True,
            allowed_hosts=["api.github.com"],
            allowed_paths=["/repos"],
            max_egress_bytes=4096,
        )
    )
    assert policy.is_full is True
    assert policy.max_egress_bytes == 4096
    assert policy.allowed_hosts == ("api.github.com",)
    assert any(match(rule, "api.github.com", "/repos/x", "GET") for rule in policy.rules)


def test_full_mode_without_allowlist_is_denied() -> None:
    """Даже с approval пустой allowlist = отказ (fail-closed)."""
    with pytest.raises(EgressDenied) as ei:
        resolve_policy(_grant(mode="full", approved=True, allowed_hosts=[]))
    assert ei.value.reason == "allowlist_empty"


# -------------------------------------------------------------- прочее


def test_path_normalization_blocks_traversal() -> None:
    """/services/../admin не должен обойти префиксную проверку."""
    assert normalize_path("/services/../admin") == "/admin"
    assert normalize_path("/services//xxx?token=secret") == "/services/xxx"
    assert normalize_path("services/x#frag") == "/services/x"
    rule = EgressRule("hooks.slack.com", "/services", "POST")
    assert match(rule, "hooks.slack.com", "/services/../admin", "POST") is False


def test_mode_unknown_is_denied_not_silently_none() -> None:
    """Неизвестный режим = ошибка конфигурации, а не тихий ``none``."""
    with pytest.raises(EgressPolicyError) as ei:
        resolve_policy(_grant(mode="allow-all"))
    assert ei.value.reason == "mode_unknown"


def test_policy_from_grant_carries_grant_id() -> None:
    """grant_id из БД попадает в политику для аудита."""
    policy = resolve_policy(_grant(mode="none", granted_by="admin"))
    assert policy.grant_id == "grant_1"
    assert EgressPolicy(mode=EgressMode.NONE).grant_id is None
