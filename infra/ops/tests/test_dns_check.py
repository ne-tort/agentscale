"""Проверка кластерного DNS: ловит мёртвый upstream-резолвер.

Инцидент 2026-10-22 (prod): агент не мог достучаться до провайдера ИИ. Всё
выглядело зелёным — поды Running, HTTP-пробы OK, TCP-egress работает, — а из
подов не резолвилось НИ ОДНО внешнее имя: CoreDNS форвардил на upstream из
/etc/resolv.conf, который после рестарта сервера оказался мёртвым (шлюз без
DNS-сервиса).

Тесты проверяют саму диагностику: правильный вердикт и что в тексте ошибки
есть инструкция, а не просто «FAIL».
"""

from __future__ import annotations

import json
import subprocess

import pytest

from prodavan_ops import dns_check
from prodavan_ops.dns_check import DnsCheckError, check_dns, coredns_timeouts


def _ok(stdout: str = "") -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(args=["kubectl"], returncode=0, stdout=stdout, stderr="")


def _fail(stderr: str = "boom") -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(args=["kubectl"], returncode=1, stdout="", stderr=stderr)


@pytest.fixture()
def fake_kubectl(monkeypatch: pytest.MonkeyPatch):
    """Подменяет kubectl: список подов, exec-резолв, логи CoreDNS."""
    state = {
        "pods": "api-0/api",
        "resolve": {"github.com": "4.225.11.194"},
        "resolve_fail": False,
        "coredns_log": "",
    }

    def _kubectl(args, **_kw):  # noqa: ANN001, ANN202
        joined = " ".join(args)
        if "get" in args and "pods" in args and "-o" in args and "json" in args:
            if state["pods"] is None:
                return _ok('{"items": []}')
            name, _, container = state["pods"].partition("/")
            return _ok(
                '{"items":[{"metadata":{"name":"%s"},'
                '"spec":{"containers":[{"name":"%s"}]},'
                '"status":{"phase":"Running"}}]}' % (name, container)
            )
        if "exec" in args:
            if state["resolve_fail"]:
                return _fail("socket.gaierror: [Errno -3] Try again")
            # имя хоста — в python-коде последнего аргумента
            host = next((h for h in state["resolve"] if h in joined), None)
            return _ok(f"{state['resolve'].get(host, '0.0.0.0')}\n")
        if "logs" in args:
            return _ok(state["coredns_log"])
        return _ok("")

    monkeypatch.setattr(dns_check, "kubectl", _kubectl)
    return state


def test_resolve_from_pod_returns_ip(fake_kubectl) -> None:
    ip = dns_check.resolve_from_pod("github.com", namespace="agentscale")
    assert ip == "4.225.11.194"


def test_resolve_from_pod_without_pod_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(dns_check, "kubectl", lambda *_a, **_k: _ok('{"items": []}'))
    with pytest.raises(DnsCheckError) as ei:
        dns_check.resolve_from_pod("github.com")
    assert "нет Running-пода" in str(ei.value)


def test_resolve_failure_reports_host(fake_kubectl) -> None:
    fake_kubectl["resolve_fail"] = True
    with pytest.raises(DnsCheckError) as ei:
        dns_check.resolve_from_pod("github.com", namespace="agentscale")
    assert "не резолвит github.com" in str(ei.value)


def test_check_dns_ok(fake_kubectl) -> None:
    resolved = check_dns(hosts=("github.com",), namespace="agentscale")
    assert resolved == {"github.com": "4.225.11.194"}


def test_check_dns_fails_when_resolve_fails(fake_kubectl) -> None:
    fake_kubectl["resolve_fail"] = True
    with pytest.raises(DnsCheckError) as ei:
        check_dns(hosts=("github.com",), namespace="agentscale")
    msg = str(ei.value)
    # Диагноз обязан нести инструкцию: иначе дежурный видит «FAIL» и не знает,
    # что CoreDNS форвардит на мёртвый upstream.
    assert "CoreDNS" in msg
    assert "forward ." in msg
    assert "ИЗ ПОДА" in msg


def test_check_dns_fails_on_coredns_timeout_flood(fake_kubectl) -> None:
    """Поток таймаутов = мёртвый upstream, даже если одно имя закэшировано."""
    fake_kubectl["coredns_log"] = "\n".join(
        "[ERROR] plugin/errors: 2 github.com. A: read udp 10.42.0.1:1->1.2.3.4:53: i/o timeout"
        for _ in range(40)
    )
    with pytest.raises(DnsCheckError) as ei:
        check_dns(hosts=("github.com",), namespace="agentscale", max_coredns_timeouts=20)
    assert "upstream-резолвер не отвечает" in str(ei.value)


def test_coredns_timeouts_counts_only_timeouts(fake_kubectl) -> None:
    fake_kubectl["coredns_log"] = (
        "[INFO] plugin/reload: ok\n"
        "[ERROR] plugin/errors: 2 x. A: read udp 1:1->2:53: i/o timeout\n"
        "[WARNING] No files matching import glob pattern\n"
        "[ERROR] plugin/errors: 2 y. A: read udp 1:1->2:53: i/o timeout\n"
    )
    assert coredns_timeouts() == 2


def test_coredns_timeouts_zero_when_logs_unavailable(fake_kubectl, monkeypatch) -> None:
    monkeypatch.setattr(dns_check, "kubectl", lambda *_a, **_k: _fail("forbidden"))
    assert coredns_timeouts() == 0


def test_timeout_flood_alone_fails_even_if_resolve_ok(fake_kubectl) -> None:
    """Резолв может пройти из кэша, но поток таймаутов — реальный симптом."""
    fake_kubectl["coredns_log"] = "[ERROR] a: read udp 1:1->2:53: i/o timeout\n" * 30
    with pytest.raises(DnsCheckError):
        check_dns(hosts=("github.com",), namespace="agentscale", max_coredns_timeouts=5)


def test_pod_in_prefers_api_pod_with_python(monkeypatch) -> None:
    payload = {
        "items": [
            {
                "metadata": {"name": "agentscale-kafka-0"},
                "spec": {"containers": [{"name": "kafka"}]},
                "status": {"phase": "Running"},
            },
            {
                "metadata": {"name": "agentscale-api-abc"},
                "spec": {"containers": [{"name": "api"}]},
                "status": {"phase": "Running"},
            },
        ]
    }
    monkeypatch.setattr(dns_check, "kubectl", lambda *_a, **_k: _ok(json.dumps(payload)))
    assert dns_check._pod_in("agentscale-dev") == "agentscale-api-abc/api"

def test_default_hosts_cover_grok_oauth() -> None:
    assert "auth.x.ai" in dns_check.REQUIRED_HOSTS

def test_resolve_passes_container_as_separate_kubectl_arg(monkeypatch) -> None:
    seen: list[list[str]] = []

    def _fake(args, **_kw):
        seen.append(list(args))
        return _ok("8.6.112.0\n")

    monkeypatch.setattr(dns_check, "kubectl", _fake)
    ip = dns_check.resolve_from_pod(
        "auth.x.ai", namespace="agentscale-dev", pod="agentscale-api-abc/api"
    )
    assert ip == "8.6.112.0"
    assert seen and seen[0][:4] == ["-n", "agentscale-dev", "exec", "agentscale-api-abc"]
    assert seen[0][4:6] == ["-c", "api"]
