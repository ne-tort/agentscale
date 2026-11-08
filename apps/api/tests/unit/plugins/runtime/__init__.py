"""Пакет unit-тестов runtime-адаптеров плагинов (писать, НЕ запускать).

Покрытие по ТЗ: ``:latest``/digest, rc/timeout/пустой вывод/JSON, KMS-fallback
без отключения проверки, ``deployments/scale``, readyReplicas=0, недоступность
k8s, CAS по ``resourceVersion``, Authorization из свежего токена, SSE без
буферизации, сборка без обязательного порта.

Verification freeze: pytest/сборки/линт/kubectl/cosign/git НЕ запускались.
"""
