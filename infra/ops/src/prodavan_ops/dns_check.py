"""Кластерный DNS: резолвят ли поды внешние имена.

Инцидент 2026-10-22 (prod): после рестарта сервера агент не мог достучаться до
провайдера ИИ. Причина — не приложение: CoreDNS форвардил на upstream из
`/etc/resolv.conf` пода (унаследован от хоста), а там оказался **мёртвый**
адрес (шлюз, на котором DNS-сервис не отвечает). Итог: из подов не
резолвилось НИ ОДНО внешнее имя, при этом:
  - хост резолвил нормально (у него свой resolv.conf),
  - TCP-egress работал (агент мог достучаться по IP),
  - `kubectl get pods` и HTTP-пробы были зелёными.

То есть снаружи всё выглядело как «сломался агент», а ломался DNS. Такая
проверка должна ловить это сразу: она резолвит имя ИЗ ПОДА (а не с хоста —
с хоста ClusterIP вообще недоступен) и смотрит свежие ошибки CoreDNS.

Проверка намеренно ходит через уже существующий под приложения: если DNS
сломан, новый pod не сможет скачать образ, и проба «не стартует» вместо
«DNS мёртв» — диагностика теряется.
"""

from __future__ import annotations

import json

from prodavan_ops.k8s import kubectl

#: Имена, которые обязаны резолвиться: провайдер ИИ — не гипотетика, а тот
#: самый случай, из-за которого инцидент и заметили. `github.com` — публичный
#: контроль (не зависит от приватных зон).
REQUIRED_HOSTS = ("github.com",)

#: Сколько последних секунд логов CoreDNS считать «свежими».
_COREDNS_LOG_WINDOW_SEC = "120s"


class DnsCheckError(RuntimeError):
    """DNS кластера не отвечает или поды не резолвят внешние имена."""


def _pod_in(namespace: str) -> str | None:
    """Первый Running-под namespace (не через shell — только kubectl json)."""
    proc = kubectl(["-n", namespace, "get", "pods", "-o", "json"])
    if proc.returncode != 0:
        return None
    try:
        items = json.loads(proc.stdout).get("items") or []
    except json.JSONDecodeError:
        return None
    for item in items:
        status = item.get("status") or {}
        if status.get("phase") != "Running":
            continue
        containers = (item.get("spec") or {}).get("containers") or []
        if not containers:
            continue
        return f"{item['metadata']['name']}/{(containers[0].get('name') or '')}"
    return None


def resolve_from_pod(
    host: str,
    *,
    namespace: str = "agentscale",
    pod: str | None = None,
    timeout_sec: float = 25.0,
) -> str:
    """Резолв `host` ИЗ ПОДА. Возвращает IP или бросает DnsCheckError."""
    target = pod or _pod_in(namespace)
    if not target:
        raise DnsCheckError(
            f"dns-check: в namespace {namespace} нет Running-пода — нечем проверить резолв"
        )
    # python есть в api-образе; shell-однострочник без кавычек-ловушек.
    code = f"import socket;print(socket.gethostbyname({host!r}))"
    proc = kubectl(
        ["-n", namespace, "exec", target, "--", "python3", "-c", code],
        subprocess_timeout=timeout_sec,
        retries=1,
    )
    out = (proc.stdout or "").strip()
    if proc.returncode != 0 or not out:
        err = (proc.stderr or proc.stdout or "").strip().splitlines()
        raise DnsCheckError(
            f"dns-check: под {target} не резолвит {host} "
            f"(exit {proc.returncode}): {err[-1] if err else 'пусто'}"
        )
    return out.splitlines()[-1]


def coredns_timeouts(*, namespace: str = "kube-system") -> int:
    """Сколько строк `i/o timeout` в СВЕЖИХ логах CoreDNS.

    Не ошибка сама по себе (бывают разовые таймауты), но их поток —
    достоверный признак мёртвого upstream: инцидент 2026-10-22 давал
    непрерывный поток таких строк на каждый запрос.
    """
    proc = kubectl(
        [
            "-n",
            namespace,
            "logs",
            "-l",
            "k8s-app=kube-dns",
            f"--since={_COREDNS_LOG_WINDOW_SEC}",
            "--tail=200",
        ],
        retries=1,
    )
    if proc.returncode != 0:
        return 0
    return sum(1 for line in (proc.stdout or "").splitlines() if "i/o timeout" in line)


def check_dns(
    *,
    namespace: str = "agentscale",
    pod: str | None = None,
    hosts: tuple[str, ...] = REQUIRED_HOSTS,
    max_coredns_timeouts: int = 20,
) -> dict[str, str]:
    """Полная проверка: резолв из пода + поток таймаутов CoreDNS.

    Возвращает {host: ip}. Бросает DnsCheckError с готовым диагнозом и
    подсказкой, что именно чинить.
    """
    resolved: dict[str, str] = {}
    failures: list[str] = []
    for host in hosts:
        try:
            resolved[host] = resolve_from_pod(host, namespace=namespace, pod=pod)
            print(f"dns-check: {host} -> {resolved[host]}")
        except DnsCheckError as exc:
            failures.append(str(exc))
            print(f"dns-check: FAIL {exc}")

    timeouts = coredns_timeouts()
    if timeouts > max_coredns_timeouts:
        failures.append(
            f"dns-check: {timeouts} строк 'i/o timeout' в логах CoreDNS за "
            f"{_COREDNS_LOG_WINDOW_SEC} — upstream-резолвер не отвечает"
        )

    if failures:
        raise DnsCheckError(
            "\n".join(failures)
            + "\n\nКак чинить (проверено на инциденте 2026-10-22):\n"
            "  1) CoreDNS форвардит на upstream из /etc/resolv.conf пода, а тот\n"
            "     унаследован от хоста. Проверьте, что хостовый nameserver жив:\n"
            "     dig +short @<host-nameserver> github.com\n"
            "  2) Если мёртв — задайте CoreDNS явный список upstream вместо\n"
            "     /etc/resolv.conf (forward . <живой> <резервные> { policy sequential }),\n"
            "     иначе один упавший резолвер выключает DNS всему кластеру.\n"
            "  3) Проверяйте резолв ИЗ ПОДА, не с хоста: с хоста ClusterIP\n"
            "     10.43.0.10 недоступен по определению и даёт ложный таймаут."
        )
    return resolved
