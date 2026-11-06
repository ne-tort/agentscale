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

#: Имена, которые обязаны резолвиться: провайдеры AI — не гипотетика, а те
#: самые случаи, из-за которых инцидент и заметили. `github.com` — публичный
#: контроль (не зависит от приватных зон), `auth.x.ai` — OAuth/refresh Grok
#: (инцидент 2026-10-29: DoT-резолвер хоста умер, xAI device-flow отдавал 502).
REQUIRED_HOSTS = ("github.com", "auth.x.ai")

#: Где искать под для проверки, в порядке приоритета. Dev-контур — основной
#: (политика сред: работаем с dev), prod — только если dev-поды не поднялись.
#: Раньше дефолтом был `agentscale`, и на чистом dev-кластере dns-check падал
#: с «нет Running-пода» — то есть выглядел как мёртвый DNS, хотя подов там
#: просто нет по построению.
DEFAULT_NAMESPACES: tuple[str, ...] = (
    "agentscale-dev",
    "agentscale-dev-sandboxes",
    "agentscale",
    "agentscale-sandboxes",
)

#: Сколько последних секунд логов CoreDNS считать «свежими».
_COREDNS_LOG_WINDOW_SEC = "120s"


class DnsCheckError(RuntimeError):
    """DNS кластера не отвечает или поды не резолвят внешние имена."""


def _pod_in(namespace: str) -> str | None:
    """Running-под с python3; предпочитаем API-под (там он точно есть).

    Не через shell — только kubectl json. Первый попавшийся pod может быть
    kafka/gotenberg без python3, и dns-check падал бы не из-за DNS.
    """
    proc = kubectl(["-n", namespace, "get", "pods", "-o", "json"])
    if proc.returncode != 0:
        return None
    try:
        items = json.loads(proc.stdout).get("items") or []
    except json.JSONDecodeError:
        return None
    candidates: list[tuple[str, str]] = []
    for item in items:
        status = item.get("status") or {}
        if status.get("phase") != "Running":
            continue
        containers = (item.get("spec") or {}).get("containers") or []
        if not containers:
            continue
        name = str(item.get("metadata", {}).get("name") or "")
        candidates.append((name, str(containers[0].get("name") or "")))
    for name, container in candidates:
        if "agentscale-api" in name:
            return f"{name}/{container}" if container else name
    if candidates:
        name, container = candidates[0]
        return f"{name}/{container}" if container else name
    return None


def resolve_from_pod(
    host: str,
    *,
    namespace: str | None = None,
    pod: str | None = None,
    timeout_sec: float = 25.0,
) -> str:
    """Резолв `host` ИЗ ПОДА. Возвращает IP или бросает DnsCheckError.

    `namespace=None` — искать под в DEFAULT_NAMESPACES по порядку (dev первым):
    команда должна работать и на чистом dev-кластере, где ns `agentscale` пуст
    или отсутствует.
    """
    target = pod
    ns = namespace
    if target is None:
        searched: list[str] = []
        for candidate in ([namespace] if namespace else DEFAULT_NAMESPACES):
            searched.append(candidate)
            target = _pod_in(candidate)
            if target:
                ns = candidate
                break
        if not target:
            raise DnsCheckError(
                "dns-check: нет Running-пода ни в одном из namespace "
                f"({', '.join(searched)}) — нечем проверить резолв. "
                "Если кластер только что поставлен, сначала дождитесь "
                "`prodavan-ops wait` (Argo ещё не раскатал платформу)."
            )
    elif ns is None:
        # Явный --pod без --namespace: берём первый dev-namespace, а не `None`.
        ns = DEFAULT_NAMESPACES[0]
    # python есть в api-образе; shell-однострочник без кавычек-ловушек.
    code = f"import socket;print(socket.gethostbyname({host!r}))"
    # `_pod_in` returns pod[/container]; kubectl exec takes them as separate
    # args (it does not accept the pod/container form used by kubectl logs).
    pod_name, _, container = target.partition("/")
    exec_args = ["-n", ns, "exec", pod_name]
    if container:
        exec_args += ["-c", container]
    exec_args += ["--", "python3", "-c", code]
    proc = kubectl(
        exec_args,
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
    namespace: str | None = None,
    pod: str | None = None,
    hosts: tuple[str, ...] = REQUIRED_HOSTS,
    max_coredns_timeouts: int = 20,
) -> dict[str, str]:
    """Полная проверка: резолв из пода + поток таймаутов CoreDNS.

    `namespace=None` — искать под по DEFAULT_NAMESPACES (dev первым).

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
            + "\n\nКак чинить (проверено на инциденте 2026-10-22; с v14 DNS декларативен):\n"
            "  1) Актуальная схема (terraform k3s-dev-host, v14-clean-install) — сначала\n"
            "     проверьте её, ручные патчи не нужны:\n"
            "     - CoreDNS: явные dns_upstreams (dev VM: 9.9.9.9 8.8.8.8; prod: туннельный\n"
            "       DNS + 8.8.8.8 1.1.1.1) — forward-патч ensure_coredns_forward из\n"
            "       post-k3s-heal.sh после КАЖДОГО старта k3s заменяет дефолт\n"
            "       `forward . /etc/resolv.conf` на `forward . <upstreams> {\n"
            "       policy sequential ... }`;\n"
            "     - хостовый резолвер: управляемый drop-in\n"
            "       /etc/systemd/resolved.conf.d/99-zz-agentscale-dns.conf\n"
            "       ([Resolve] DNS=<те же upstream'ы>, null_resource.host_dns);\n"
            "     - kubelet/k3s: resolv-conf указывает на /etc/k3s-resolv.conf\n"
            "       (переменная k3s_resolv_conf) с прямыми upstream'ами, а не на хостовый\n"
            "       /etc/resolv.conf.\n"
            "     Проверка:\n"
            "       kubectl -n kube-system get cm coredns -o jsonpath='{.data.Corefile}'\n"
            "     — в `forward .` должны стоять dns_upstreams, не /etc/resolv.conf;\n"
            "     terraform apply идемпотентен и возвращает всю схему в декларированное\n"
            "     состояние.\n"
            "  2) Если схема сломана руками или хост легаси: CoreDNS форвардит на upstream\n"
            "     из /etc/resolv.conf пода, а тот унаследован от хоста. Проверьте, что\n"
            "     хостовый nameserver жив:\n"
            "     dig +short @<host-nameserver> github.com\n"
            "  3) Если мёртв — задайте CoreDNS явный список upstream вместо\n"
            "     /etc/resolv.conf (forward . <живой> <резервные> { policy sequential }),\n"
            "     иначе один упавший резолвер выключает DNS всему кластеру.\n"
            "  4) Проверяйте резолв ИЗ ПОДА, не с хоста: с хоста ClusterIP\n"
            "     10.43.0.10 недоступен по определению и даёт ложный таймаут."
        )
    return resolved
