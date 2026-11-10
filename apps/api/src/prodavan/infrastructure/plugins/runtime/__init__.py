"""Runtime-адаптеры плагинов (F11): cosign / k8s scale+readiness / CAS / proxy.

Публичный контракт слоя — ТОЛЬКО этот модуль. Родитель подключает так::

    from prodavan.infrastructure.plugins.runtime import (
        build_runtime_ports,
    )

    runtime = build_runtime_ports(settings)
    platform = build_plugin_platform(
        settings,
        grant_store=...,
        admin_ports=...,
        **runtime.as_platform_overrides(),
    )

Имена override'ов в :meth:`RuntimePorts.as_platform_overrides` в ТОЧНОСТИ
совпадают с ``PORT_OVERRIDES`` из ``wiring/platform.py``: ``cosign``,
``activation_scale``, ``activation_readiness``, ``activation_state_store``,
``activation_proxy``, ``gateway_backend_http``.

Все адаптеры fail-closed: отсутствие обязательного порта = явная ошибка
композиции, недоступность cosign/k8s/ConfigMap/пробы Ready = явный отказ, а не
«тихий успех». Секреты, токены, подписи, DSN и kubeconfig в логи/аудит не
попадают (§4.5 v4, критерий №11).

Риски, зафиксированные для родителя:
* self-hosted OIDC (keyless Fulcio) -> KMS-fallback с ЗАФИКСИРОВАННЫМ trust
  root; отключать проверку подписи ЗАПРЕЩЕНО (критерий №18);
* in-cluster kubeconfig: нужен SA token + CA, вне кластера адаптер отказывает;
* CAS-гонки: ``resourceVersion`` один на весь ConfigMap, поэтому CAS разных
  инстансов конфликтуют даже при разных ключах ``data``.
"""

from __future__ import annotations

from .build import (
    PLATFORM_PORT_NAMES,
    RUNTIME_OVERRIDES,
    RuntimePorts,
    build_runtime_ports,
)
from .config import (
    DEFAULT_STATE_CONFIGMAP,
    ENV_GATEWAY_URL,
    ENV_IMAGE_PULL_SECRET,
    ENV_KMS_KEY_REF,
    ENV_NAMESPACE,
    InternalGatewayUrl,
    RuntimeConfig,
)
from .cosign_adapter import (
    FORBIDDEN_COSIGN_FLAGS,
    AttestationEvidence,
    AttestationVerifier,
    CliCosignPort,
    assert_verification_enabled,
    attestation_command,
    attestations_from_cosign,
    audit_fields,
    keyless_verify_command,
    kms_verify_command,
    prepare_ref,
    verify_cosign_stdout,
)
from .deployment_admin import (
    ArtifactProvenanceError,
    DeploymentAdminError,
    DeploymentConfigurationError,
    DeploymentConflictError,
    DeploymentNotFoundError,
    DeploymentOwnershipError,
    DeploymentSecretError,
    DeploymentStateError,
    DeploymentUnavailableError,
    K8sDeploymentAdmin,
)
from .secret_projection import SecretProjectionPort, SecretReference
from .token_provider import (
    ServiceAccountTokenError,
    ServiceAccountTokenProvider,
    ServiceAccountTokenProviderPort,
    TokenProviderClosedError,
    TokenProviderConfigurationError,
    TokenRequestError,
)
from .errors import PluginRuntimeError, RuntimeConfigError
from .k8s_scale import (
    ConfigMapStateStore,
    K8sApiClient,
    K8sApiConflict,
    K8sApiError,
    K8sApiNotFound,
    K8sApiPort,
    K8sApiUnavailable,
    K8sScalePort,
    ProbeResult,
    ReadinessMode,
    ServiceHealthProbe,
    StateStoreUnavailable,
    decode_state,
    default_deployment_name,
    encode_state,
    normalize_k8s_name,
    plugin_deployment_name,
    plugin_resource_name,
    plugin_service_name,
    state_key,
)
from .proxy import (
    HEADER_AUTHORIZATION,
    TRUSTED_X_PLUGIN_HEADERS,
    BackendSenderPort,
    HeldRequest,
    HttpProxyPort,
    ProxyConfigurationError,
    build_backend_request,
    build_outgoing_headers,
    plugin_service_url,
)

__all__ = [
    # композиция (главная точка входа)
    "PLATFORM_PORT_NAMES",
    "RUNTIME_OVERRIDES",
    "RuntimePorts",
    "build_runtime_ports",
    # admin deployment lifecycle
    "ArtifactProvenanceError",
    "DeploymentAdminError",
    "DeploymentConfigurationError",
    "DeploymentConflictError",
    "DeploymentNotFoundError",
    "DeploymentOwnershipError",
    "DeploymentSecretError",
    "DeploymentStateError",
    "DeploymentUnavailableError",
    "K8sDeploymentAdmin",
    "SecretProjectionPort",
    "SecretReference",
    "ServiceAccountTokenError",
    "ServiceAccountTokenProvider",
    "ServiceAccountTokenProviderPort",
    "TokenProviderClosedError",
    "TokenProviderConfigurationError",
    "TokenRequestError",
    # конфигурация / ошибки
    "DEFAULT_STATE_CONFIGMAP",
    "ENV_GATEWAY_URL",
    "ENV_IMAGE_PULL_SECRET",
    "ENV_KMS_KEY_REF",
    "ENV_NAMESPACE",
    "InternalGatewayUrl",
    "RuntimeConfig",
    "PluginRuntimeError",
    "RuntimeConfigError",
    # cosign
    "FORBIDDEN_COSIGN_FLAGS",
    "AttestationEvidence",
    "AttestationVerifier",
    "CliCosignPort",
    "assert_verification_enabled",
    "attestation_command",
    "attestations_from_cosign",
    "audit_fields",
    "keyless_verify_command",
    "kms_verify_command",
    "prepare_ref",
    "verify_cosign_stdout",
    # k8s scale / readiness / CAS
    "ConfigMapStateStore",
    "K8sApiClient",
    "K8sApiConflict",
    "K8sApiError",
    "K8sApiNotFound",
    "K8sApiPort",
    "K8sApiUnavailable",
    "K8sScalePort",
    "ProbeResult",
    "ReadinessMode",
    "ServiceHealthProbe",
    "StateStoreUnavailable",
    "decode_state",
    "default_deployment_name",
    "encode_state",
    "normalize_k8s_name",
    "plugin_deployment_name",
    "plugin_resource_name",
    "plugin_service_name",
    "state_key",
    # proxy
    "HEADER_AUTHORIZATION",
    "TRUSTED_X_PLUGIN_HEADERS",
    "BackendSenderPort",
    "HeldRequest",
    "HttpProxyPort",
    "ProxyConfigurationError",
    "build_backend_request",
    "build_outgoing_headers",
    "plugin_service_url",
]
