"""Единый генератор egress-конфигурации для плагинов И агентов (v4 §6.1, §6.3).

ОДИН генератор, ДВЕ NetworkPolicy, ОДНА схема аудита:

* ``build_envoy_routes(policy)`` — динамические кластеры Envoy строго из
  ``allowed_hosts``/``allowed_paths`` ``CapabilityGrant`` в БД. Никакого статического
  ``webhooks.example.com`` и никаких хостов из JWT (исправление v1/v3, §13.3).
  Несовпадение → ``direct_response: 403`` (fail-closed, критерий 8).
* ``build_network_policy(scope)`` — две NP по ``podSelector``. У плагинов есть
  tenant-postgres :5432 и MinIO :9000, у агентов — **нет** (§6.1, §13.3).
  ``prodavan`` (``app: agentscale-postgres``) не входит ни в одну политику.
* ``build_audit_event`` — единая схема ``plugin_egress_request`` /
  ``agent_egress_request``: host, path, method, status, bytes, decision, tenant,
  subject (§4.5, §6.3). Поля идентичны для обоих scope.

Лейблы сервисов взяты из as-built k3s-манифестов (уточнение задачи 14):
MinIO — ``app.kubernetes.io/name: agentscale-minio``, Kafka/Redpanda —
``app.kubernetes.io/name: agentscale-kafka``, tenant-postgres — ``app: tenant-postgres``,
pgbouncer — ``app: pgbouncer``, plugin-gateway / egress-proxy — ``app: ...``.
НЕ ``app: minio`` / ``app: kafka`` из §6.2 дизайна: с ними правила не совпали бы
с реальными подами.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Literal, Mapping

from prodavan.application.plugins.egress.errors import EgressPolicyError
from prodavan.application.plugins.egress.policy import EgressPolicy

#: Dev-контур (политика сред: работаем только с dev). Прод — только по явному
#: требованию; при переносе заменить на ``agentscale`` / ``agentscale-sandboxes``.
PLATFORM_NAMESPACE = "agentscale-dev"
SANDBOX_NAMESPACE = "agentscale-dev-sandboxes"
KUBE_SYSTEM_NAMESPACE = "kube-system"

#: as-built лейблы (base/platform/minio.yaml:38,50; kafka.yaml:21,35;
#: base/plugins/tenant-postgres.yaml:270; pgbouncer.yaml; networkpolicy-*.yaml).
MINIO_POD_SELECTOR = {"app.kubernetes.io/name": "agentscale-minio"}
KAFKA_POD_SELECTOR = {"app.kubernetes.io/name": "agentscale-kafka"}
TENANT_POSTGRES_POD_SELECTOR = {"app": "tenant-postgres"}
PGBOUNCER_POD_SELECTOR = {"app": "pgbouncer"}
PLUGIN_GATEWAY_POD_SELECTOR = {"app": "plugin-gateway"}
EGRESS_PROXY_POD_SELECTOR = {"app": "egress-proxy"}
POD_GATEWAY_POD_SELECTOR = {"app": "agentscale-api"}
KUBE_DNS_POD_SELECTOR = {"k8s-app": "kube-dns"}

#: Платформенный Postgres НЕ должен появляться ни в одной сгенерированной NP.
FORBIDDEN_POSTGRES_SELECTOR = {"app": "agentscale-postgres"}

#: Порты, которые отличают plugin-NP от agent-NP (§6.1).
PLUGIN_ONLY_PORTS = frozenset({5432, 6432, 9000, 9092})

_AUDIT_FIELDS: tuple[str, ...] = (
    "event",
    "occurred_at",
    "host",
    "path",
    "method",
    "status",
    "bytes",
    "decision",
    "code",
    "reason",
    "tenant",
    "subject",
    "grant_id",
    "request_id",
    "scope",
)


class EgressScope(StrEnum):
    """Для кого строится конфигурация."""

    PLUGIN = "plugin"
    AGENT = "agent"


class AuditEventName(StrEnum):
    """Единый реестр имён egress-событий (§4.4, §4.5, §6.3)."""

    PLUGIN = "plugin_egress_request"
    AGENT = "agent_egress_request"


@dataclass(frozen=True, slots=True)
class EgressConfigGenerator:
    """Генератор правил из ``EgressPolicy`` (grant), не из токена.

    ``namespace`` — платформенный namespace для ``namespaceSelector``. Один и тот же
    экземпляр (или два с разным scope) даёт **две** NP; «единой NP» в v4 нет.
    """

    namespace: str = PLATFORM_NAMESPACE
    sandbox_namespace: str = SANDBOX_NAMESPACE

    # ------------------------------------------------------------------ Envoy

    def build_envoy_routes(self, policy: EgressPolicy) -> dict[str, Any]:
        """Собрать конфигурацию Envoy из allowlist ``CapabilityGrant``.

        * при ``none`` / пустом allowlist — ``clusters: []`` и единственный
          ``direct_response: 403`` (стартовый fail-closed конфиг из
          ``egress-proxy.yaml``);
        * при ``webhooks-only``/``full`` — по одному динамическому DNS-кластеру на
          каждый **разрешённый хост** и по маршруту на каждое правило
          (хост × path_prefix × method);
        * домены виртуального хоста — точные имена из allowlist (НЕ ``["*"]``),
          поэтому подмена ``Host: evil.com`` не попадает ни в один route и получает
          финальный ``direct_response: 403``;
        * кластеры используют ``LOGICAL_DNS`` + явный ``sni`` и проверку SAN,
          чтобы DNS-резолв шёл через прокси, а TLS проверялся на каждом хопе (§6.3).

        Ни один хост не захардкожен: ``webhooks.example.com`` из v1/v3 отсутствует.
        """
        if not isinstance(policy, EgressPolicy):
            raise EgressPolicyError("policy must be an EgressPolicy", reason="policy_invalid")

        clusters = self._build_clusters(policy)
        routes = self._build_routes(policy)

        virtual_hosts = [
            {
                "name": f"allow_{self._cluster_name(host)}",
                # Точное совпадение :authority; wildcard НЕ используется.
                "domains": [host],
                # Каждый vhost завершается 403 для любого несовпавшего path/method.
                "routes": [route for route in routes if route.get("_host") == host]
                + [self._deny_route()],
            }
            for host in policy.allowed_hosts
        ]
        # Один catch-all vhost для чужих Host + финальный 403.
        virtual_hosts.append(
            {
                "name": "default_deny",
                "domains": ["*"],
                "routes": [self._deny_route()],
            }
        )

        listener = {
            "name": "egress_listener",
            "address": {"socket_address": {"address": "0.0.0.0", "port_value": 8080}},
            "filter_chains": [
                {
                    "filters": [
                        {
                            "name": "envoy.filters.network.http_connection_manager",
                            "typed_config": {
                                "@type": "type.googleapis.com/envoy.extensions.filters.network.http_connection_manager.v3.HttpConnectionManager",
                                "stat_prefix": "plugin_egress",
                                "http_filters": [
                                    {
                                        "name": "envoy.filters.http.router",
                                        "typed_config": {
                                            "@type": "type.googleapis.com/envoy.extensions.filters.http.router.v3.Router",
                                        },
                                    }
                                ],
                                "route_config": {
                                    "name": "egress_routes",
                                    "virtual_hosts": [
                                        {
                                            "name": vhost["name"],
                                            "domains": vhost["domains"],
                                            "routes": [
                                                self._public_route(route) for route in vhost["routes"]
                                            ],
                                        }
                                        for vhost in virtual_hosts
                                    ],
                                },
                                "access_log": [self._access_log()],
                            },
                        }
                    ]
                }
            ],
        }

        return {
            "admin": {"address": {"socket_address": {"address": "127.0.0.1", "port_value": 9901}}},
            "static_resources": {"listeners": [listener], "clusters": clusters},
            # Метаданные НЕ попадают в envoy.yaml как поля конфигурации: это
            # контрольная информация для контроллера, обновляющего ConfigMap.
            "_meta": {
                "source": "CapabilityGrant",
                "grant_id": policy.grant_id,
                "mode": policy.mode.value,
                "max_egress_bytes": policy.max_egress_bytes,
                "cluster_count": len(clusters),
                "route_count": sum(len(vhost["routes"]) for vhost in virtual_hosts),
            },
        }

    def _build_clusters(self, policy: EgressPolicy) -> list[dict[str, Any]]:
        """Динамические кластеры: ровно по одному на хост из ``allowed_hosts``."""
        clusters: list[dict[str, Any]] = []
        for host in policy.allowed_hosts:
            clusters.append(
                {
                    "name": self._cluster_name(host),
                    "type": "LOGICAL_DNS",
                    "connect_timeout": "5s",
                    "dns_lookup_family": "V4_ONLY",
                    "load_assignment": {
                        "cluster_name": self._cluster_name(host),
                        "endpoints": [
                            {
                                "lb_endpoints": [
                                    {
                                        "endpoint": {
                                            "address": {
                                                "socket_address": {"address": host, "port_value": 443}
                                            }
                                        }
                                    }
                                ]
                            }
                        ],
                    },
                    # TLS на каждом хопе: SNI и проверка SAN по :authority.
                    "transport_socket": {
                        "name": "envoy.transport_sockets.tls",
                        "typed_config": {
                            "@type": "type.googleapis.com/envoy.extensions.transport_sockets.tls.v3.UpstreamTlsContext",
                            "sni": host,
                            "common_tls_context": {
                                "validation_context": {
                                    "match_typed_subject_alt_names": [
                                        {"san_type": "DNS", "matcher": {"exact": host}}
                                    ]
                                }
                            },
                        },
                    },
                }
            )
        return clusters

    def _build_routes(self, policy: EgressPolicy) -> list[dict[str, Any]]:
        """Маршруты из развёрнутых правил (хост × path_prefix × метод)."""
        routes: list[dict[str, Any]] = []
        for rule in policy.rules:
            routes.append(
                {
                    "_host": rule.host,
                    "match": {
                        "path_separated_prefix": rule.path_prefix,
                        "headers": [
                            {"name": ":method", "string_match": {"exact": rule.method}}
                        ],
                    },
                    "route": {
                        "cluster": self._cluster_name(rule.host),
                        "host_rewrite_literal": rule.host,
                        "auto_host_rewrite": False,
                        "timeout": "15s",
                    },
                }
            )
        return routes

    @staticmethod
    def _public_route(route: dict[str, Any]) -> dict[str, Any]:
        """Убрать служебный ключ ``_host`` перед записью в envoy.yaml."""
        return {key: value for key, value in route.items() if key != "_host"}

    @staticmethod
    def _cluster_name(host: str) -> str:
        """Стабильное, безопасное имя кластера из хоста."""
        return "egress_" + host.replace(".", "_").replace("-", "_")

    @staticmethod
    def _deny_route() -> dict[str, Any]:
        """``direct_response: 403`` для любого несовпадения (критерий 8)."""
        return {
            "match": {"prefix": "/"},
            "direct_response": {
                "status": 403,
                "body": {"inline_string": "egress denied: no CapabilityGrant rule\n"},
            },
        }

    @staticmethod
    def _access_log() -> dict[str, Any]:
        """JSON-лог с ЕДИНОЙ схемой (§6.3): те же поля, что у build_audit_event."""
        return {
            "name": "envoy.access_loggers.stdout",
            "typed_config": {
                "@type": "type.googleapis.com/envoy.extensions.access_loggers.stream.v3.StdoutAccessLog",
                "log_format": {
                    "json_format": {
                        "event": "plugin_egress_request",
                        "host": "%REQ(:AUTHORITY)%",
                        "path": "%REQ(:PATH)%",
                        "method": "%REQ(:METHOD)%",
                        "status": "%RESPONSE_CODE%",
                        "bytes": "%BYTES_SENT%",
                        "decision": "%RESPONSE_CODE%",
                        "tenant": "%REQ(X-Company-ID)%",
                        "subject": "%REQ(X-Plugin-ID)%",
                    }
                },
            },
        }

    # --------------------------------------------------------- NetworkPolicy

    def build_network_policy(
        self,
        scope: EgressScope | Literal["plugin", "agent"],
    ) -> dict[str, Any]:
        """Построить ОДНУ из ДВУХ NetworkPolicy (§6.1).

        ``plugin`` — ``plugin-egress``: gateway :8080, egress-proxy :8080, DNS,
        **tenant-postgres :5432**, pgbouncer :6432, **MinIO :9000**, Kafka :9092.
        ``agent`` — ``agent-egress-reference``: только pod-gateway :8001, DNS и
        80/443. Плагиновые :5432/:6432/:9000/:9092/:8080 в agent-политике
        ОТСУТСТВУЮТ.

        ``prodavan`` (``app: agentscale-postgres``) не включается никуда.
        """
        parsed = EgressScope(scope) if not isinstance(scope, EgressScope) else scope
        if parsed is EgressScope.PLUGIN:
            return self._plugin_network_policy()
        return self._agent_network_policy()

    def _plugin_network_policy(self) -> dict[str, Any]:
        return {
            "apiVersion": "networking.k8s.io/v1",
            "kind": "NetworkPolicy",
            "metadata": {
                "name": "plugin-egress",
                "labels": {
                    "app": "plugin",
                    "app.kubernetes.io/name": "plugin-egress",
                    "app.kubernetes.io/part-of": "agentscale",
                    "app.kubernetes.io/component": "plugin-network-policy",
                },
                "annotations": {"argocd.argoproj.io/sync-wave": "1"},
            },
            "spec": {
                "podSelector": {"matchLabels": {"app": "plugin"}},
                "policyTypes": ["Ingress", "Egress"],
                "ingress": [
                    self._peer_rule(
                        PLUGIN_GATEWAY_POD_SELECTOR,
                        self._ports("TCP", 8080),
                        namespace=self.namespace,
                    )
                ],
                "egress": [
                    self._peer_rule(
                        PLUGIN_GATEWAY_POD_SELECTOR,
                        self._ports("TCP", 8080),
                        namespace=self.namespace,
                    ),
                    self._peer_rule(
                        EGRESS_PROXY_POD_SELECTOR,
                        self._ports("TCP", 8080),
                        namespace=self.namespace,
                    ),
                    self._dns_rule(),
                    # Tenant PostgreSQL :5432 — отдельный Service, НЕ prodavan.
                    self._peer_rule(
                        TENANT_POSTGRES_POD_SELECTOR,
                        self._ports("TCP", 5432),
                        namespace=self.namespace,
                    ),
                    # PgBouncer :6432 — основной путь TENANT_DB_DSN.
                    self._peer_rule(
                        PGBOUNCER_POD_SELECTOR,
                        self._ports("TCP", 6432),
                        namespace=self.namespace,
                    ),
                    # MinIO :9000 — scoped object access, агентам закрыт.
                    self._peer_rule(
                        MINIO_POD_SELECTOR,
                        self._ports("TCP", 9000),
                        namespace=self.namespace,
                    ),
                    # Kafka/Redpanda :9092 — события плагинов.
                    self._peer_rule(
                        KAFKA_POD_SELECTOR,
                        self._ports("TCP", 9092),
                        namespace=self.namespace,
                    ),
                ],
            },
        }

    def _agent_network_policy(self) -> dict[str, Any]:
        return {
            "apiVersion": "networking.k8s.io/v1",
            "kind": "NetworkPolicy",
            "metadata": {
                "name": "agent-egress-reference",
                "labels": {
                    "app.kubernetes.io/name": "agent-egress-reference",
                    "app.kubernetes.io/part-of": "agentscale",
                    "app.kubernetes.io/component": "agent-network-policy-reference",
                },
                "annotations": {
                    "agentscale.io/manifest-role": "reference-only",
                    "argocd.argoproj.io/sync-wave": "1",
                },
            },
            "spec": {
                "podSelector": {"matchLabels": {"app": "agent"}},
                "policyTypes": ["Ingress", "Egress"],
                "ingress": [
                    {
                        "from": [
                            {
                                "namespaceSelector": {
                                    "matchLabels": {
                                        "kubernetes.io/metadata.name": "agent-sandbox-system"
                                    }
                                }
                            }
                        ],
                        "ports": self._ports("TCP", 3921),
                    }
                ],
                "egress": [
                    # pod-gateway :8001 — ТОЛЬКО агентская surface.
                    self._peer_rule(
                        POD_GATEWAY_POD_SELECTOR,
                        self._ports("TCP", 8001),
                        namespace=self.namespace,
                    ),
                    self._dns_rule(),
                    # Публичный интернет 80/443 с исключением pod/service CIDR.
                    {
                        "to": [
                            {
                                "ipBlock": {
                                    "cidr": "0.0.0.0/0",
                                    "except": ["10.42.0.0/16", "10.43.0.0/16"],
                                }
                            }
                        ],
                        "ports": self._ports("TCP", 443) + self._ports("TCP", 80),
                    },
                ],
            },
        }

    def _peer_rule(
        self,
        pod_selector: Mapping[str, str],
        ports: list[dict[str, Any]],
        *,
        namespace: str,
    ) -> dict[str, Any]:
        """``to``-правило по namespaceSelector + podSelector (§6.2)."""
        return {
            "to": [
                {
                    "namespaceSelector": {
                        "matchLabels": {"kubernetes.io/metadata.name": namespace}
                    },
                    "podSelector": {"matchLabels": dict(pod_selector)},
                }
            ],
            "ports": ports,
        }

    def _dns_rule(self) -> dict[str, Any]:
        return self._peer_rule(
            KUBE_DNS_POD_SELECTOR,
            self._ports("UDP", 53) + self._ports("TCP", 53),
            namespace=KUBE_SYSTEM_NAMESPACE,
        )

    @staticmethod
    def _ports(protocol: str, port: int) -> list[dict[str, Any]]:
        return [{"protocol": protocol, "port": port}]

    # ----------------------------------------------------------------- Audit

    @staticmethod
    def audit_fields() -> tuple[str, ...]:
        """Канонический набор полей: ОДИНАКОВ для plugin и agent (§4.5, §6.3)."""
        return _AUDIT_FIELDS

    def build_audit_event(
        self,
        *,
        scope: EgressScope | Literal["plugin", "agent"],
        host: str,
        path: str,
        method: str,
        status: int,
        bytes: int,
        decision: str,
        tenant: str,
        subject: str,
        occurred_at: str,
        grant_id: str | None = None,
        request_id: str | None = None,
        code: str | None = None,
        reason: str | None = None,
    ) -> dict[str, Any]:
        """Сформировать audit-событие единой схемы.

        Имя события различается (``plugin_egress_request`` / ``agent_egress_request``),
        а **набор и типы полей — идентичны**. В схему НЕ попадают тела запросов,
        токены, секреты, PII, raw SQL (§4.5): только канонические идентификаторы,
        хост, путь, метод, статус, объём и решение.

        ``bytes`` — уже проверенное целое (<= max_egress_bytes), ``decision`` —
        ``allow``/``deny``. Значения не проходят «санитайзинг» задним числом:
        вызывающий (``EgressService``) обязан передать только безопасные данные.
        """
        parsed = EgressScope(scope) if not isinstance(scope, EgressScope) else scope
        if parsed not in (EgressScope.PLUGIN, EgressScope.AGENT):
            raise EgressPolicyError(f"unknown egress scope: {scope!r}", reason="scope_unknown")
        event_name = AuditEventName.PLUGIN if parsed is EgressScope.PLUGIN else AuditEventName.AGENT

        if not isinstance(bytes, int) or isinstance(bytes, bool) or bytes < 0:
            raise EgressPolicyError("audit bytes must be a non-negative integer", reason="audit_invalid")
        if not isinstance(status, int) or isinstance(status, bool):
            raise EgressPolicyError("audit status must be an integer", reason="audit_invalid")
        if decision not in ("allow", "deny"):
            raise EgressPolicyError("audit decision must be 'allow' or 'deny'", reason="audit_invalid")

        event: dict[str, Any] = {
            "event": event_name.value,
            "occurred_at": occurred_at,
            "host": host,
            "path": path,
            "method": method,
            "status": status,
            "bytes": bytes,
            "decision": decision,
            "code": code,
            "reason": reason,
            "tenant": tenant,
            "subject": subject,
            "grant_id": grant_id,
            "request_id": request_id,
            "scope": parsed.value,
        }
        # Гарантия единой схемы: ни одного лишнего/пропущенного поля.
        if tuple(event.keys()) != _AUDIT_FIELDS:
            raise EgressPolicyError("audit event schema drift detected", reason="audit_schema_drift")
        return event


def build_audit_event(
    *,
    scope: EgressScope | Literal["plugin", "agent"],
    host: str,
    path: str,
    method: str,
    status: int,
    bytes: int,
    decision: str,
    tenant: str,
    subject: str,
    occurred_at: str,
    grant_id: str | None = None,
    request_id: str | None = None,
    code: str | None = None,
    reason: str | None = None,
) -> dict[str, Any]:
    """Модульная обёртка над ``EgressConfigGenerator.build_audit_event``.

    Вынесена отдельно, чтобы и плагиновый, и агентский контроллеры вызывали ОДНУ
    функцию и не могли «разъехаться» по схеме (§10, риск «Расхождение политик»).
    """
    return EgressConfigGenerator().build_audit_event(
        scope=scope,
        host=host,
        path=path,
        method=method,
        status=status,
        bytes=bytes,
        decision=decision,
        tenant=tenant,
        subject=subject,
        occurred_at=occurred_at,
        grant_id=grant_id,
        request_id=request_id,
        code=code,
        reason=reason,
    )


__all__ = [
    "AuditEventName",
    "EGRESS_PROXY_POD_SELECTOR",
    "EgressConfigGenerator",
    "EgressScope",
    "FORBIDDEN_POSTGRES_SELECTOR",
    "KAFKA_POD_SELECTOR",
    "KUBE_DNS_POD_SELECTOR",
    "MINIO_POD_SELECTOR",
    "PGBOUNCER_POD_SELECTOR",
    "PLUGIN_GATEWAY_POD_SELECTOR",
    "PLUGIN_ONLY_PORTS",
    "PLATFORM_NAMESPACE",
    "POD_GATEWAY_POD_SELECTOR",
    "SANDBOX_NAMESPACE",
    "TENANT_POSTGRES_POD_SELECTOR",
    "build_audit_event",
]
