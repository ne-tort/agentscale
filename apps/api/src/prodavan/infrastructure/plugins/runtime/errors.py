"""Ошибки runtime-адаптеров плагинов (F11, слой инфраструктуры).

Контракт fail-closed
--------------------
Ни один адаптер не имеет «тихого» фолбэка. Отсутствие бинарника cosign,
недоступность k8s API, 404 Deployment, не отвечающая проба ``/health``,
конфликт ``resourceVersion`` ConfigMap, отсутствие обязательного порта при
сборке — всё это ЯВНАЯ ошибка, а не «считаем успехом» / «вернём None».

Иерархия намеренно тонкая:
* ``PluginRuntimeError`` — база для ошибок композиции/конфигурации
  (``RuntimeError``: startup обязан упасть, а не деградировать);
* ``RuntimeConfigError`` — неполная/противоречивая конфигурация;
* ``ScaleUnavailable`` / ``NotReady`` / ``StateStoreUnavailable`` импортируются
  из ``application.plugins.activation.errors`` и НЕ дублируются: activator
  (``_apply_scale``, ``_read_replicas``) ловит ``Exception``, но коды доменных
  ошибок должны остаться стабильными для Gateway/Admin API (§4.5 v4).

В ``detail``/``extra`` ЗАПРЕЩЕНЫ токены, подписи, DSN, kubeconfig, тела
запросов, содержимое stdout cosign и PII (§4.5, критерий №11).
"""

from __future__ import annotations

__all__ = [
    "PluginRuntimeError",
    "RuntimeConfigError",
]


class PluginRuntimeError(RuntimeError):
    """Ошибка композиции runtime-портов: startup НЕ должен продолжаться.

    Наследник ``RuntimeError`` (как ``PluginPlatformConfigError`` из
    ``wiring/config.py``), а не ``AppError``: это не отказ HTTP-запроса, а
    неспособность собрать платформу. Родитель обязан уронить startup, а не
    подключить «почти собранную» платформу с молчаливым allow.
    """

    def __init__(self, detail: str) -> None:
        if not isinstance(detail, str) or not detail.strip():
            raise ValueError("PluginRuntimeError requires a non-empty detail")
        super().__init__(detail)
        self.detail = detail


class RuntimeConfigError(PluginRuntimeError):
    """Конфигурация runtime-адаптеров неполна или противоречива.

    Возбуждается ``RuntimeConfig.from_env`` / ``build_runtime_ports`` при
    отсутствии namespace, пустом trust root, запрещённом ``:latest``,
    отключении проверки подписи и т.п. Никаких «подставим дефолт из
    соседнего контура» (§1 v4, ADR-008).
    """
