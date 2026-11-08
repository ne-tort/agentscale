"""Тесты чистых функций имён (§2.1/§2.2/§3.3/§5.4).

Покрывают требуемые пункты задания F3:
  * нормализация/отвержение ``co_123; DROP DATABASE prodavan``;
  * имя БД ≤ 63;
  * ``search_path`` без версии плагина;
  * явная ошибка на пустой/невалидный id;
  * защита от инъекций в DDL (никакой конкатенации непроверенных строк);
  * детерминированность (grant/revoke/uninstall бьют в один объект).
"""

from __future__ import annotations

import pytest

from prodavan.application.plugins.tenantdb import (
    INVALID_IDENTIFIER,
    MAX_COMPANY_ID_BYTES,
    MAX_PLUGIN_ID_BYTES,
    PG_MAX_IDENTIFIER_BYTES,
    TenantDbError,
    assert_safe_identifier,
    bucket_name,
    normalize_identifier,
    plugin_role_name,
    plugin_schema_name,
    plugin_search_path,
    tenant_db_name,
)

# region Базовые имена
def test_tenant_db_name_format() -> None:
    """``tenant_<norm>`` — ровно так, как в §2.1/§13.2."""
    assert tenant_db_name("co_123") == "tenant_co_123"
    assert tenant_db_name("CO_123") == "tenant_co_123"  # lower()
    assert tenant_db_name("  co_123  ") == "tenant_co_123"
    assert tenant_db_name("acme") == "tenant_acme"


def test_plugin_role_name_format() -> None:
    """``tenant_<company>_plugin_<plugin>`` — уникальна в паре (§2.2)."""
    assert plugin_role_name("co_123", "mod_equipment") == "tenant_co_123_plugin_mod_equipment"
    # Тот же плагин у другой компании → другая роль (нет cross-tenant доступа).
    assert plugin_role_name("co_999", "mod_equipment") != plugin_role_name("co_123", "mod_equipment")


def test_plugin_schema_name_has_no_version() -> None:
    """§3.3: версия в имени схемы НЕ используется."""
    assert plugin_schema_name("mod_equipment") == "module_mod_equipment"
    assert plugin_search_path("mod_equipment") == "module_mod_equipment"
    # Никаких v1/v2/semver-суффиксов в контракте имени.
    assert "v1" not in plugin_schema_name("mod_equipment")
    assert "1.2" not in plugin_schema_name("mod_equipment")
    # search_path === имя схемы: без запятых, без public, без кавычек.
    assert plugin_search_path("mod_equipment") == plugin_schema_name("mod_equipment")
    assert "," not in plugin_search_path("mod_equipment")
    assert "public" not in plugin_search_path("mod_equipment")


def test_bucket_name_format() -> None:
    """``tenant-<company>`` — bucket-per-tenant (§5.4)."""
    assert bucket_name("co_123") == "tenant-co_123"
    assert bucket_name("co_123") != tenant_db_name("co_123")  # разные разделители
# endregion


# region Отвержение инъекций
INJECTION_PAYLOADS = [
    "co_123; DROP DATABASE prodavan",
    "co_123'; DROP DATABASE prodavan; --",
    'co_123" ; DROP SCHEMA public CASCADE; --',
    "co_123\nDROP DATABASE prodavan",
    "co-123",
    "co.123",
    "co 123",
    "co/123",
    "co\\123",
    "co@123",
    "co$123",
    "CO_123;SELECT 1",
    "';--",
    "co_123/*x*/",
    "со_123",  # кириллица
    "co_123\x00",
]
# ``tenant_prodavan`` / ``prodavan`` — валидные [a-z0-9_] id, НЕ инъекции:
# имя БД ``tenant_prodavan`` физически отличается от платформенной ``prodavan``.
VALID_BUT_SUSPICIOUS_IDS = ["tenant_prodavan", "prodavan", "co__123", "_co_123"]


@pytest.mark.parametrize("payload", INJECTION_PAYLOADS)
def test_reject_injection_in_db_name(payload: str) -> None:
    """Главный кейс задания: ``co_123; DROP DATABASE prodavan`` ОТВЕРГНУТ.

    Явная ошибка, а не тихая замена на ``_``: иначе ``co-1`` и ``co_1``
    сколлидились бы в одну БД.
    """
    with pytest.raises(TenantDbError) as ei:
        tenant_db_name(payload)
    assert ei.value.code == INVALID_IDENTIFIER
    # САМ payload НЕ должен отразиться в сообщении (защита логов).
    assert payload not in str(ei.value)


@pytest.mark.parametrize("payload", INJECTION_PAYLOADS)
def test_reject_injection_in_plugin_id(payload: str) -> None:
    with pytest.raises(TenantDbError) as ei:
        plugin_schema_name(payload)
    assert ei.value.code == INVALID_IDENTIFIER
    assert payload not in str(ei.value)


@pytest.mark.parametrize("payload", INJECTION_PAYLOADS)
def test_reject_injection_in_role_and_bucket(payload: str) -> None:
    with pytest.raises(TenantDbError):
        plugin_role_name(payload, "mod_ok")
    with pytest.raises(TenantDbError):
        plugin_role_name("co_ok", payload)
    with pytest.raises(TenantDbError):
        bucket_name(payload)


def test_assert_safe_identifier_is_defense_in_depth() -> None:
    """``sql.py`` перепроверяет уже собранное имя перед подстановкой в DDL."""
    assert assert_safe_identifier("tenant_co_123") == "tenant_co_123"
    for bad in ('tenant_co"; DROP DATABASE prodavan; --', "Tenant_Co", "", None, 123, "a" * 64):
        with pytest.raises(TenantDbError) as ei:
            assert_safe_identifier(bad)
        assert ei.value.code == INVALID_IDENTIFIER


def test_no_silent_sanitization() -> None:
    """Валидатор, а не «санитайзер»: подозрительная строка не «чинится»."""
    # Если бы была тихая нормализация, результат равнялся бы имени валидного id.
    with pytest.raises(TenantDbError):
        normalize_identifier("co_123; DROP DATABASE prodavan")


@pytest.mark.parametrize("company", VALID_BUT_SUSPICIOUS_IDS)
def test_suspicious_but_valid_id_never_targets_platform_db(company: str) -> None:
    """Даже ``prodavan`` как company_id даёт ``tenant_prodavan``, НЕ ``prodavan``.

    Префикс ``tenant_`` — часть конструктивной границы §2.1: плагин
    физически не может получить DSN к платформенной БД через подбор id.
    """
    name = tenant_db_name(company)
    assert name.startswith("tenant_")
    assert name != "prodavan"
    # Имя схемы тоже всегда с префиксом module_, без «голых» имён.
    assert plugin_schema_name("prodavan") == "module_prodavan"
    assert plugin_schema_name("prodavan").startswith("module_")
# endregion


# region Пустые/невалидные/нестроковые id
@pytest.mark.parametrize("bad", ["", "   ", "_", "-", None, 0, 1, [], {}, b"co_123", True])
def test_reject_empty_and_non_string(bad: object) -> None:
    for fn in (tenant_db_name, bucket_name, plugin_schema_name, normalize_identifier):
        with pytest.raises(TenantDbError) as ei:
            fn(bad)
        assert ei.value.code == INVALID_IDENTIFIER


def test_error_shape_is_safe() -> None:
    """У ошибки есть стабильный код/статус, и в ней нет DSN/пароля."""
    with pytest.raises(TenantDbError) as ei:
        tenant_db_name("co_123; DROP DATABASE prodavan")
    err = ei.value
    assert err.code == INVALID_IDENTIFIER
    assert err.http_status == 422
    assert err.operation == "tenant_db_name"
    assert "DROP DATABASE" not in str(err)
    assert "password" not in str(err).lower()
# endregion


# region Лимит 63 байта (PostgreSQL limit)
def test_db_name_max_63_bytes() -> None:
    """Имя БД ≤ 63 байта при максимально допустимом company_id."""
    company = "c" * MAX_COMPANY_ID_BYTES
    name = tenant_db_name(company)
    assert len(name.encode("utf-8")) == PG_MAX_IDENTIFIER_BYTES
    assert len(name.encode("utf-8")) <= 63


def test_db_name_over_limit_is_explicit_error_not_truncation() -> None:
    """Превышение → ЯВНАЯ ошибка, а не молчаливое ``[:63]`` (коллизии!)."""
    company = "c" * (MAX_COMPANY_ID_BYTES + 1)
    with pytest.raises(TenantDbError) as ei:
        tenant_db_name(company)
    assert ei.value.code == INVALID_IDENTIFIER
    # Убеждаемся, что усечения нет: имя не вернулось вообще.


def test_schema_name_max_63_bytes() -> None:
    plugin = "p" * MAX_PLUGIN_ID_BYTES
    assert len(plugin_schema_name(plugin).encode("utf-8")) == PG_MAX_IDENTIFIER_BYTES
    with pytest.raises(TenantDbError):
        plugin_schema_name("p" * (MAX_PLUGIN_ID_BYTES + 1))


def test_role_name_length_checked() -> None:
    """Сумма company+plugin+префиксов тоже обязана уложиться в 63."""
    ok = "c" * 20 + "p" * 20
    assert len(plugin_role_name(ok[:20], ok[20:]).encode("utf-8")) <= 63
    with pytest.raises(TenantDbError):
        plugin_role_name("c" * MAX_COMPANY_ID_BYTES, "p" * MAX_PLUGIN_ID_BYTES)


def test_bucket_name_length_checked() -> None:
    with pytest.raises(TenantDbError):
        bucket_name("c" * (MAX_COMPANY_ID_BYTES + 1))
# endregion


# region Детерминированность
def test_names_are_deterministic() -> None:
    """revoke/uninstall должны попасть в тот же объект, что и grant."""
    args = ("co_123", "mod_equipment")
    assert tenant_db_name(args[0]) == tenant_db_name(args[0])
    assert plugin_role_name(*args) == plugin_role_name(*args)
    assert plugin_schema_name(args[1]) == plugin_schema_name(args[1])
    assert plugin_search_path(args[1]) == plugin_schema_name(args[1])


def test_all_names_ascii_safe() -> None:
    """Итоговые имена содержат только [a-z0-9_] (bucket — константный `-`)."""
    import re

    for name in (
        tenant_db_name("co_123"),
        plugin_role_name("co_123", "mod_equipment"),
        plugin_schema_name("mod_equipment"),
        plugin_search_path("mod_equipment"),
    ):
        assert re.fullmatch(r"[a-z0-9_]+", name), name
    assert re.fullmatch(r"[a-z0-9_-]+", bucket_name("co_123"))
# endregion
