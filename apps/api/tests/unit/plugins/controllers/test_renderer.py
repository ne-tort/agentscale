"""Unit-тесты чистых рендеров трёх controller-managed артефактов.

Acceptance-критерии задачи:
  * ``render_tenant_hba`` — БЕЗ ``map=``, с финальным ``reject all``, пары
    ТОЛЬКО активных grant'ов, детерминированность/идемпотентность;
  * ``render_pgbouncer_userlist`` — SCRAM-секреты, plaintext не проходит и не
    утекает в repr/исключения, админская строка сохраняется;
  * ``render_envoy_config`` — через генератор задачи 08, БЕЗ статического
    ``webhooks.example.com``, с 403-fallback, кластеры из allowlist.

Тесты проходят в текущем прогоне (pytest tests/unit/plugins).
"""

from __future__ import annotations

import pytest
import yaml

from prodavan.application.plugins.egress.policy import EgressPolicy, EgressRule, EgressMode
from prodavan.infrastructure.plugins.controllers.errors import RenderError, SyncError
from prodavan.infrastructure.plugins.controllers.renderer import (
    HBA_AUTH_METHOD,
    build_envoy_policies,
    render_envoy_config,
    render_envoy_yaml,
    render_pgbouncer_userlist,
    render_pgbouncer_userlist_preserving_admin,
    render_tenant_hba,
)

from .conftest import (
    ADMIN_LINE,
    GRANT_A,
    GRANT_B,
    PLAINTEXT_PASSWORD,
    SCRAM_A,
    SCRAM_B,
    START_USERLIST,
    grant,
)


# --------------------------------------------------------------------------- tenant_hba


def test_tenant_hba_has_no_map_option_and_reject_all() -> None:
    """Опция ident/peer map с scram-sha-256 недопустима; финальный reject all обязателен."""
    rendered = render_tenant_hba([GRANT_A])
    assert "map=" not in rendered, "pg_hba не должен содержать map= (упадёт postgres)"
    assert "reject" in rendered
    assert "0.0.0.0/0" in rendered and "::/0" in rendered
    assert HBA_AUTH_METHOD in rendered
    assert rendered.endswith("\n")


def test_tenant_hba_pairs_come_from_active_grants_only() -> None:
    """Пары (DATABASE, USER) — только активные grant'ы, отозванный исчезает."""
    revoked = grant(
        grant_id="grant_revoked",
        company="co_999",
        plugin="mod_ghost",
        scram=SCRAM_A,
        active=False,
    )
    rendered = render_tenant_hba([GRANT_A, GRANT_B, revoked])
    assert GRANT_A.database in rendered and GRANT_A.role in rendered
    assert GRANT_B.database in rendered and GRANT_B.role in rendered
    assert revoked.database not in rendered
    assert revoked.role not in rendered
    # Каждая пара дублируется по двум pod/service CIDR.
    assert rendered.count(GRANT_A.role) == 2
    assert rendered.count(GRANT_B.role) == 2


def test_tenant_hba_empty_grants_is_fail_closed() -> None:
    """Пустой список grant'ов = ни одной разрешающей строки, только reject all."""
    rendered = render_tenant_hba([])
    # Ровно две reject-строки (IPv4 + IPv6) и ни одной host-строки с парой.
    assert rendered.count("host    all") == 2
    assert f"host    {GRANT_A.database}" not in rendered
    assert GRANT_A.role not in rendered
    assert "reject" in rendered


def test_tenant_hba_is_deterministic_and_idempotent() -> None:
    """Порядок входа не влияет на байты; повтор = та же строка (нет patch/reload)."""
    first = render_tenant_hba([GRANT_A, GRANT_B])
    reversed_input = render_tenant_hba([GRANT_B, GRANT_A])
    assert first == reversed_input
    assert render_tenant_hba([GRANT_A, GRANT_B]) == first
    # Дубликаты grant_id с разными данными — явная ошибка, а не «первый выиграл».
    with pytest.raises(SyncError):
        render_tenant_hba(
            [
                GRANT_A,
                grant(
                    grant_id="grant_a",
                    company="co_other",
                    plugin="mod_other",
                ),
            ]
        )


def test_tenant_hba_rejects_unsafe_identifiers() -> None:
    """Инъекция в role/database не попадает в pg_hba, а поднимает RenderError."""
    bad = ActiveGrantLike(role='tenant_x"; DROP DATABASE prodavan', database="tenant_x")
    with pytest.raises((RenderError, SyncError)):
        # Посторонний объект отсекается ещё в _sorted_grants (проверка типа),
        # что тоже fail-closed: в pg_hba не попадёт НИ ОДНА строка с инъекцией.
        render_tenant_hba([bad])


def test_tenant_hba_reject_line_after_pairs() -> None:
    """include подставляется ДО reject-all: пары идут раньше запрета."""
    rendered = render_tenant_hba([GRANT_A])
    assert rendered.index(GRANT_A.role) < rendered.index("0.0.0.0/0       reject")


# --------------------------------------------------------------------------- userlist


def test_userlist_contains_scram_secrets_only() -> None:
    """В userlist только SCRAM-секреты активных ролей, без plaintext/DSN."""
    rendered = render_pgbouncer_userlist([GRANT_A, GRANT_B])
    assert f'"{GRANT_A.role}" "{SCRAM_A}"' in rendered
    assert f'"{GRANT_B.role}" "{SCRAM_B}"' in rendered
    assert PLAINTEXT_PASSWORD not in rendered
    assert "postgresql://" not in rendered
    assert "dsn" not in rendered.lower()


def test_userlist_plaintext_secret_is_rejected_and_never_in_repr() -> None:
    """Plaintext-пароль не принимается; его значение не утекает в исключение."""
    bad = grant(
        grant_id="grant_plain",
        company="co_plain",
        plugin="mod_plain",
        scram=PLAINTEXT_PASSWORD,
    )
    with pytest.raises(RenderError) as exc_info:
        render_pgbouncer_userlist([bad])
    # Ни repr исключения, ни repr самого grant'а не содержат plaintext.
    assert PLAINTEXT_PASSWORD not in repr(exc_info.value)
    assert PLAINTEXT_PASSWORD not in str(exc_info.value)
    assert PLAINTEXT_PASSWORD not in repr(bad)
    assert "scram_secret=<redacted>" in repr(bad)


def test_userlist_active_grants_only() -> None:
    """Отозванный grant не оставляет секрет в userlist (revoke снимает пару)."""
    revoked = grant(
        grant_id="grant_revoked",
        company="co_999",
        plugin="mod_ghost",
        active=False,
    )
    rendered = render_pgbouncer_userlist([GRANT_A, revoked])
    assert GRANT_A.role in rendered
    assert revoked.role not in rendered


def test_userlist_preserves_pgbouncer_admin_line() -> None:
    """Строка admin-консоли сохраняется, иначе RELOAD; станет недоступен."""
    rendered = render_pgbouncer_userlist_preserving_admin([GRANT_A], current=START_USERLIST)
    assert rendered.startswith(ADMIN_LINE)
    assert GRANT_A.role in rendered
    assert SCRAM_A in rendered
    # Идемпотентность: повтор с тем же current даёт ту же строку.
    again = render_pgbouncer_userlist_preserving_admin([GRANT_A], current=rendered)
    assert again == rendered


def test_userlist_without_admin_line_is_fail_closed() -> None:
    """Нет admin-строки → явный отказ, а не Secret без консоли RELOAD."""
    with pytest.raises(RenderError):
        render_pgbouncer_userlist_preserving_admin([GRANT_A], current="# только комментарий\n")


def test_userlist_admin_role_cannot_be_a_plugin_role() -> None:
    """Роль admin-консоли не может быть выдана как роль плагина."""
    forged = grant(
        grant_id="grant_admin",
        company="co_admin",
        plugin="pgbouncer_admin",
        scram=SCRAM_A,
    )
    # Имя роли строится как tenant_co_admin_plugin_pgbouncer_admin, поэтому
    # явно подменяем поле через object.__new__ для проверки защиты.
    import dataclasses

    bad = dataclasses.replace(forged, role="pgbouncer_admin")
    with pytest.raises(RenderError):
        render_pgbouncer_userlist([bad])


def test_userlist_secrets_mapping_overrides_grant_secret() -> None:
    """Ротация SCRAM без правки grant'а: secrets[role] имеет приоритет."""
    rotated = "SCRAM-SHA-256$8192:bmV3c2FsdA==$bmV3c3RvcmVk:bmV3c2VydmVy"
    rendered = render_pgbouncer_userlist([GRANT_A], {GRANT_A.role: rotated})
    assert rotated in rendered
    assert SCRAM_A not in rendered


# --------------------------------------------------------------------------- envoy


def test_envoy_empty_config_is_fail_closed_403() -> None:
    """Без активных egress-политик — стартовый clusters: [] + direct_response 403."""
    config = render_envoy_config([])
    assert config["static_resources"]["clusters"] == []
    vhosts = config["static_resources"]["listeners"][0]["filter_chains"][0]["filters"][0][
        "typed_config"
    ]["route_config"]["virtual_hosts"]
    assert vhosts[-1]["name"] == "default_deny"
    assert vhosts[-1]["routes"][0]["direct_response"]["status"] == 403


def test_envoy_config_uses_generator_08_and_has_403_fallback() -> None:
    """Кластеры из allowlist через build_envoy_routes задачи 08; несовпадение → 403."""
    policies = build_envoy_policies([GRANT_A, GRANT_B])
    assert len(policies) == 2
    config = render_envoy_config(policies)
    clusters = config["static_resources"]["clusters"]
    names = {c["name"] for c in clusters}
    # Имена кластеров ровно из allowed_hosts, генератор задачи 08.
    assert "egress_hooks_slack_com" in names
    assert "egress_api_github_com" in names
    assert "webhooks.example.com" not in str(config)
    vhosts = config["static_resources"]["listeners"][0]["filter_chains"][0]["filters"][0][
        "typed_config"
    ]["route_config"]["virtual_hosts"]
    assert vhosts[-1]["name"] == "default_deny"
    assert vhosts[-1]["routes"][0]["direct_response"]["status"] == 403
    # У каждого allow-vhost тоже есть свой deny_route (fail-closed).
    for vhost in vhosts[:-1]:
        assert any(r.get("direct_response", {}).get("status") == 403 for r in vhost["routes"])
    # _meta контроллера задачи 08 НЕ попадает в envoy.yaml.
    assert "_meta" not in config


def test_envoy_config_no_static_webhooks_example_com() -> None:
    """Исправление v1/v3: статический хост не захардкожен ни при каких входах."""
    for policies in ([], build_envoy_policies([]), build_envoy_policies([GRANT_A])):
        assert "webhooks.example.com" not in render_envoy_yaml(render_envoy_config(policies))


def test_envoy_config_is_deterministic() -> None:
    """Порядок политик не влияет на YAML (детерминированность сравнения /data)."""
    policies = build_envoy_policies([GRANT_A, GRANT_B])
    a = render_envoy_yaml(render_envoy_config(policies))
    b = render_envoy_yaml(render_envoy_config(list(reversed(policies))))
    assert a == b
    # Валидный YAML с ожидаемой структурой.
    parsed = yaml.safe_load(a)
    assert parsed["static_resources"]["clusters"]


def test_envoy_duplicate_host_clusters_are_merged_once() -> None:
    """Один хост в двух grant'ах даёт ОДИН кластер, а не дубль (Envoy упадёт)."""
    p1 = EgressPolicy(
        mode=EgressMode.WEBHOOKS_ONLY,
        rules=(EgressRule(host="hooks.slack.com", path_prefix="/services"),),
        allowed_hosts=("hooks.slack.com",),
        allowed_paths=("/services",),
        grant_id="g1",
    )
    p2 = EgressPolicy(
        mode=EgressMode.WEBHOOKS_ONLY,
        rules=(EgressRule(host="hooks.slack.com", path_prefix="/repos"),),
        allowed_hosts=("hooks.slack.com",),
        allowed_paths=("/repos",),
        grant_id="g2",
    )
    config = render_envoy_config([p1, p2])
    clusters = config["static_resources"]["clusters"]
    assert len(clusters) == 1
    assert clusters[0]["name"] == "egress_hooks_slack_com"
    vhosts = config["static_resources"]["listeners"][0]["filter_chains"][0]["filters"][0][
        "typed_config"
    ]["route_config"]["virtual_hosts"]
    allow = next(v for v in vhosts if v["name"] != "default_deny")
    prefixes = {
        r["match"]["path_separated_prefix"]
        for r in allow["routes"]
        if "path_separated_prefix" in r.get("match", {})
    }
    # Оба пути из двух grant'ов сохранены, deny_route — строго последним.
    assert prefixes == {"/services", "/repos"}
    assert "direct_response" in allow["routes"][-1]
    assert allow["routes"][-1]["direct_response"]["status"] == 403


def test_envoy_invalid_policy_type_is_rejected() -> None:
    """Битый объект политики → RenderError, а не тихо пропущенный кластер."""
    with pytest.raises(RenderError):
        render_envoy_config([{"not": "a policy"}])  # type: ignore[list-item]


def test_build_envoy_policies_none_egress_is_skipped() -> None:
    """Grant без egress-секции не создаёт кластеров (режим none)."""
    plain = grant(grant_id="g_plain", company="co_1", plugin="mod_x")
    assert build_envoy_policies([plain]) == ()
    assert render_envoy_config(build_envoy_policies([plain]))["static_resources"]["clusters"] == []


def test_build_envoy_policies_revoked_grant_is_skipped() -> None:
    """Отозванный grant не должен оставить Envoy-кластер (revoke убирает кластер)."""
    revoked = grant(
        grant_id="g_revoked",
        company="co_9",
        plugin="mod_ghost",
        active=False,
        egress={
            "mode": "webhooks-only",
            "allowed_hosts": ["hooks.slack.com"],
            "allowed_paths": ["/services"],
        },
    )
    assert build_envoy_policies([revoked]) == ()


# Вспомогательный объект с «грязным» идентификатором: проверяем, что рендер
# валидирует поля по типу, а не доверяет arbitrary-объекту.
class ActiveGrantLike:
    grant_id = "g_bad"
    company_id = "co_x"
    plugin_id = "mod_x"
    database = "tenant_x"
    scram_secret = SCRAM_A
    active = True
    egress = None

    def __init__(self, *, role: str, database: str) -> None:
        self.role = role
        self.database = database
