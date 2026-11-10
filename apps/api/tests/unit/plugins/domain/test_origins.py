"""Канонические server-owned origin'ы — граница доверия iframe плагина.

``exact_https_origin``/``exact_plugin_origins`` задают статический origin
плагина и shell origin, от которых зависят CSP ``frame-ancestors``, DTO
bootstrap и handoff в ``iframe.name``. До этого модуля прямых тестов не было:
проверялись только следствия в других слоях, поэтому ослабление проверки
прошло бы незамеченным.
"""

from __future__ import annotations

import pytest

from prodavan.domain.plugins.origins import (
    PluginOriginError,
    exact_https_origin,
    exact_plugin_origins,
)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("https://plugins.example.test", "https://plugins.example.test"),
        ("https://plugins.example.test/", "https://plugins.example.test"),
        ("https://plugins.example.test:443", "https://plugins.example.test"),
        ("https://plugins.example.test:8443", "https://plugins.example.test:8443"),
    ],
)
def test_canonical_https_origin_accepts_only_exact_forms(value: str, expected: str) -> None:
    assert exact_https_origin(value, setting="PLUGIN_STATIC_ORIGIN") == expected


@pytest.mark.parametrize(
    "value",
    [
        "",
        " https://plugins.example.test",
        "https://plugins.example.test ",
        "https://*.example.test",
        "https://plugins.example.test/path",
        "https://plugins.example.test?a=1",
        "https://plugins.example.test#frag",
        "https://user:pass@plugins.example.test",
        "http://plugins.example.test",
        "https://Plugins.Example.Test",
        "https://\u00e9xample.test",
        "//plugins.example.test",
        "https://plugins.example.test//",
    ],
)
def test_non_canonical_or_unsafe_origins_fail_closed(value: str) -> None:
    with pytest.raises(PluginOriginError):
        exact_https_origin(value, setting="PLUGIN_STATIC_ORIGIN")


@pytest.mark.parametrize("value", [None, 443, b"https://x.test", []])
def test_non_string_origin_fails_closed(value: object) -> None:
    with pytest.raises(PluginOriginError):
        exact_https_origin(value, setting="PLUGIN_STATIC_ORIGIN")


def test_plugin_origins_must_be_distinct() -> None:
    same = "https://agentscale.dev"
    with pytest.raises(PluginOriginError, match="must be distinct"):
        exact_plugin_origins(static_origin=same, shell_origin=same)


def test_plugin_origins_are_returned_canonically() -> None:
    static, shell = exact_plugin_origins(
        static_origin="https://plugin-origin.agentscale.dev/",
        shell_origin="https://agentscale.dev:443",
    )
    assert static == "https://plugin-origin.agentscale.dev"
    assert shell == "https://agentscale.dev"


def test_distinct_domains_but_same_effective_origin_is_rejected() -> None:
    """Разные записи одного origin'а не дают изоляции: сравнивается канон."""
    with pytest.raises(PluginOriginError, match="must be distinct"):
        exact_plugin_origins(
            static_origin="https://agentscale.dev:443",
            shell_origin="https://agentscale.dev",
        )
