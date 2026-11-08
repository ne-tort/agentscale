"""Сверка сгенерированной NetworkPolicy с as-built манифестами задачи 14 (v4 §6.1-§6.3).

ЗАЧЕМ ЭТОТ МОДУЛЬ
------------------
Ранний дизайн (§6.2 v1/v3) использовал выдуманные селекторы ``app: minio`` и
``app: kafka``. Реальные поды помечены иначе:
``app.kubernetes.io/name: agentscale-minio`` / ``agentscale-kafka``
(задача 14, ``infra/k3s/base/platform/{minio,kafka}.yaml``). NP с выдуманными
лейблами «успешно» применяется, но не совпадает НИ С ОДНИМ подом — правила молча
не работают. Это не баг, который видно в логах: это тихая дыра в фаерволе.

Поэтому здесь НЕ «просто проверка», а **барьер применения**:
``assert_generated_policy_matches_as_built`` либо доказывает полное совпадение
селекторов/портов/namespace с эталоном задачи 14, либо бросает
``PolicyAlignmentError`` и применение политики ЗАПРЕЩАЕТСЯ. Единственная
санкционированная точка применения — ``apply_generated_policy_guarded``:
сначала assert, и только потом вызов ``apply``.

``AS_BUILT_SELECTORS`` — ЕДИНЫЙ источник истины лейблов для этой сверки.
Каждое значение подтверждено конкретным ``файл:строка`` (см.
``AS_BUILT_PROVENANCE``). Генератор задачи 08 держит свои константы
(``MINIO_POD_SELECTOR`` и др.) — этот модуль их НЕ импортирует и НЕ правит, а
ловит расхождение, если генератор «уедет» обратно к дизайну дословно.

Вторая ось: ``assert_policy_source_is_grant`` запрещает резолвить egress-правила
из полей JWT (``token.egress`` / ``claims.allowed_hosts``). Источник — только
``CapabilityGrant`` в БД по ``grant_id`` (§4.2, §6.1, §6.3, критерий №22):
истечение короткого TTL не должно пересобирать сетевые правила посреди запроса.

Всё fail-closed: отсутствие данных, неизвестный scope, битый документ,
недоступный эталонный манифест, неподтверждённая цепочка резолвинга = ЯВНЫЙ
отказ, а не молчаливое «наверное, совпадает».

Комментарии и тексты ошибок — на русском, идентификаторы — на английском.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from pathlib import Path
from types import MappingProxyType
from typing import Any, Callable

from prodavan.application.plugins.egress.errors import EgressPolicyError

#: Статус/код problem+json для ошибки несовпадения политики.
POLICY_ALIGNMENT_ERROR_CODE = "POLICY_ALIGNMENT_FAILED"

#: Единственный допустимый источник egress-правил (§6.1, §6.3, §13.3).
GRANT_SOURCE = "CapabilityGrant"

#: Dev-контур (политика сред: работаем только с dev). Prod — только по явному
#: требованию; тогда вызывающий передаёт ``platform_namespace="agentscale"``.
PLATFORM_NAMESPACE = "agentscale-dev"
KUBE_SYSTEM_NAMESPACE = "kube-system"

# ---------------------------------------------------------------------------
# ЕДИНЫЙ ИСТОЧНИК ИСТИНЫ ЛЕЙБЛОВ
# ---------------------------------------------------------------------------

#: ``podSelector.matchLabels`` реальных подов. Ключ — логическое имя сервиса.
#: Значения заморожены: «подправить лейбл на ходу» нельзя, правка = новый
#: коммит с обновлением ``AS_BUILT_PROVENANCE`` и манифестов задачи 14.
AS_BUILT_SELECTORS: Mapping[str, Mapping[str, str]] = MappingProxyType(
    {
        "minio": MappingProxyType({"app.kubernetes.io/name": "agentscale-minio"}),
        "kafka": MappingProxyType({"app.kubernetes.io/name": "agentscale-kafka"}),
        "tenant-postgres": MappingProxyType({"app": "tenant-postgres"}),
        "pgbouncer": MappingProxyType({"app": "pgbouncer"}),
        "plugin-gateway": MappingProxyType({"app": "plugin-gateway"}),
        "egress-proxy": MappingProxyType({"app": "egress-proxy"}),
        "kube-dns": MappingProxyType({"k8s-app": "kube-dns"}),
    }
)

#: Где КАЖДЫЙ лейбл подтверждён as-built кодом (для таблицы в
#: ``docs/migration/plugin-network-alignment.md`` и для сообщений об ошибке).
AS_BUILT_PROVENANCE: Mapping[str, str] = MappingProxyType(
    {
        "minio": "infra/k3s/base/platform/minio.yaml:38,50,67,71; "
        "infra/k3s/base/plugins/networkpolicy-plugin.yaml:134",
        "kafka": "infra/k3s/base/platform/kafka.yaml:21,35,55,59; "
        "infra/k3s/base/plugins/networkpolicy-plugin.yaml:147",
        "tenant-postgres": "infra/k3s/base/plugins/tenant-postgres.yaml:275,279; "
        "infra/k3s/base/plugins/networkpolicy-plugin.yaml:109",
        "pgbouncer": "infra/k3s/base/plugins/pgbouncer.yaml:150,154; "
        "infra/k3s/base/plugins/networkpolicy-plugin.yaml:121",
        "plugin-gateway": "infra/k3s/base/plugins/plugin-gateway.yaml:163,167; "
        "infra/k3s/base/plugins/networkpolicy-plugin.yaml:54,67",
        "egress-proxy": "infra/k3s/base/plugins/egress-proxy.yaml:141,145; "
        "infra/k3s/base/plugins/networkpolicy-plugin.yaml:79",
        "kube-dns": "infra/k3s/base/plugins/networkpolicy-plugin.yaml:91; "
        "infra/k3s/base/plugins/networkpolicy-agent.yaml:77",
    }
)

#: Селектор агентской surface (:8001, pod-gateway). В ``AS_BUILT_SELECTORS``
#: не входит — это не цель egress плагинов, а цель агентской NP.
AGENT_POD_GATEWAY_SELECTOR: Mapping[str, str] = MappingProxyType(
    {"app": "agentscale-api"}
)
AGENT_POD_GATEWAY_PROVENANCE = (
    "infra/k3s/base/plugins/networkpolicy-agent.yaml:66; "
    "infra/k3s/base/agentscale-api/deployment.yaml:36"
)

#: Выдуманные лейблы раннего дизайна и платформенный Postgres (prodavan).
#: Появление любого из них в NP = немедленный запрет применения.
FORBIDDEN_SELECTORS: Mapping[str, Mapping[str, str]] = MappingProxyType(
    {
        "legacy-minio": MappingProxyType({"app": "minio"}),
        "legacy-kafka": MappingProxyType({"app": "kafka"}),
        "prodavan-postgres": MappingProxyType({"app": "agentscale-postgres"}),
    }
)

#: Эталонные манифесты задачи 14, с которыми сверяется сгенерированная NP.
AS_BUILT_MANIFEST_PATHS: Mapping[str, str] = MappingProxyType(
    {
        "plugin": "infra/k3s/base/plugins/networkpolicy-plugin.yaml",
        "agent": "infra/k3s/base/plugins/networkpolicy-agent.yaml",
    }
)

#: Имена NP и ``podSelector`` subject'а по scope (§6.1: ДВЕ политики).
EXPECTED_NP_NAMES: Mapping[str, str] = MappingProxyType(
    {"plugin": "plugin-egress", "agent": "agent-egress-reference"}
)
EXPECTED_SUBJECT_SELECTORS: Mapping[str, Mapping[str, str]] = MappingProxyType(
    {
        "plugin": MappingProxyType({"app": "plugin"}),
        "agent": MappingProxyType({"app": "agent"}),
    }
)

#: Порты, обязательные в plugin-NP (§6.2, задача 14 networkpolicy-plugin.yaml).
PLUGIN_REQUIRED_PORTS = frozenset({8080, 53, 5432, 6432, 9000, 9092})
#: Порты, которых в plugin-NP быть НЕ должно (:8001 — agent-surface, :80/:443 —
#: прямой выход в интернет в обход egress-proxy, §6.3).
PLUGIN_FORBIDDEN_PORTS = frozenset({8001, 80, 443})
#: Порты, которых в agent-NP быть НЕ должно (§6.1, §13.3, задача 14).
AGENT_FORBIDDEN_PORTS = frozenset({5432, 6432, 9000, 9092, 8080})

#: Поля JWT, из которых ЗАПРЕЩЕНО строить egress-правила (§4.2, §13.3).
FORBIDDEN_JWT_EGRESS_FIELDS = frozenset(
    {"egress", "allowed_hosts", "allowed_paths", "quotas", "max_egress_bytes"}
)

#: Маркеры JWT-источника в цепочке резолвинга. Наличие любого = отказ.
#: Порядок ВАЖЕН: более специфичные маркеры идут РАНЬШЕ, иначе
#: ``claims.egress`` перехватит ``plugin_claims.egress`` как подстроку и в
#: сообщении об ошибке будет указан не тот селектор.
FORBIDDEN_JWT_CHAIN_MARKERS: tuple[str, ...] = (
    "plugin_claims.egress",
    "token.egress",
    "claims.egress",
    "jwt.egress",
    "token.allowed_hosts",
    "claims.allowed_hosts",
    "jwt.allowed_hosts",
    "token.allowed_paths",
    "claims.allowed_paths",
    "token.quotas",
    "claims.quotas",
    "token.max_egress_bytes",
    "claims.max_egress_bytes",
)

#: Маркеры, ДОКАЗЫВАЮЩИЕ резолвинг по grant. Хотя бы один обязателен.
GRANT_CHAIN_MARKERS: tuple[str, ...] = (
    "grant_id",
    "capabilitygrant",
    "grant_store",
    "grantstoreport",
    "get_grant",
    "grantlookup",
    "resolve_policy",
)


class PolicyAlignmentError(EgressPolicyError):
    """Сгенерированная политика НЕ совпадает с as-built: применение ЗАПРЕЩЕНО.

    Наследник ``EgressPolicyError`` (-> ``EgressDenied`` -> ``AppError``), чтобы
    HTTP-слой отдал problem+json без доп. маппинга. В сообщении ВСЕГДА указан
    конкретный селектор (``selector``) и scope — иначе «что-то не совпало»
    бесполезно при разборе инцидента.
    """

    def __init__(
        self,
        detail: str,
        *,
        selector: str,
        scope: str,
        reason: str,
    ) -> None:
        super().__init__(
            detail,
            reason=reason,
            status=422,
            extra={"selector": selector, "scope": scope},
        )
        self.selector = selector
        self.scope = scope
        self.reason = reason
        # Перекрываем код/заголовок родителя: это отдельный класс инцидента.
        self.code = POLICY_ALIGNMENT_ERROR_CODE
        self.title = "Generated Policy Does Not Match As-Built Manifests"


# ---------------------------------------------------------------------------
# Вспомогательные функции (все — fail-closed)
# ---------------------------------------------------------------------------


def _fmt_selector(selector: Mapping[str, str]) -> str:
    """Читаемое представление селектора для сообщения об ошибке."""
    return "{" + ", ".join(f"{k}: {v}" for k, v in sorted(selector.items())) + "}"


def _selector_key(selector: Mapping[str, Any]) -> tuple[tuple[str, str], ...]:
    """Канонический ключ селектора (независим от порядка ключей)."""
    return tuple(sorted((str(k), str(v)) for k, v in selector.items()))


def _normalize_scope(scope: Any) -> str:
    """Привести scope к ``plugin``/``agent``; всё прочее — отказ.

    Принимает и ``str``, и ``EgressScope`` (StrEnum) из задачи 08, и любой
    объект с ``.value``: модуль намеренно НЕ импортирует генератор, чтобы не
    зависеть от файла, который ещё находится в работе.
    """
    raw = getattr(scope, "value", scope)
    if not isinstance(raw, str):
        raise PolicyAlignmentError(
            f"scope должен быть 'plugin' или 'agent', получено {scope!r}",
            selector="<документ>",
            scope=str(scope),
            reason="scope_unknown",
        )
    value = raw.strip().lower()
    if value not in ("plugin", "agent"):
        raise PolicyAlignmentError(
            f"неизвестный scope {scope!r}; применение политики запрещено",
            selector="<документ>",
            scope=value,
            reason="scope_unknown",
        )
    return value


def _expected_selectors(scope: str) -> dict[str, Mapping[str, str]]:
    """Эталонный набор ``podSelector`` для данного scope."""
    if scope == "plugin":
        return {name: AS_BUILT_SELECTORS[name] for name in AS_BUILT_SELECTORS}
    # Агент: только pod-gateway :8001 и kube-dns (остальное — ipBlock 80/443).
    return {
        "pod-gateway": AGENT_POD_GATEWAY_SELECTOR,
        "kube-dns": AS_BUILT_SELECTORS["kube-dns"],
    }


def _peers(np_dict: Mapping[str, Any]) -> list[tuple[str, Mapping[str, Any]]]:
    """Все peer-элементы из ingress/egress правил NP.

    Собираем и ``from``, и ``to`` для обоих направлений: в текущем генераторе
    задачи 08 ingress-правило плагина приходит с ключом ``to`` (особенность
    ``_peer_rule``), а в эталонном манифесте задачи 14 — с ``from``. Для сверки
    СЕЛЕКТОРОВ это несущественно; структурное расхождение ``from``/``to``
    вынесено в отдельное наблюдение (см. docs/migration/plugin-network-alignment.md).
    """
    spec = np_dict.get("spec")
    if not isinstance(spec, Mapping):
        raise PolicyAlignmentError(
            "у NP отсутствует spec; применение запрещено",
            selector="<документ>",
            scope="<unknown>",
            reason="document_invalid",
        )
    result: list[tuple[str, Mapping[str, Any]]] = []
    for direction in ("ingress", "egress"):
        rules = spec.get(direction) or []
        if isinstance(rules, Mapping) or not isinstance(rules, Sequence) or isinstance(rules, (str, bytes)):
            raise PolicyAlignmentError(
                f"spec.{direction} должен быть списком правил",
                selector="<документ>",
                scope="<unknown>",
                reason="document_invalid",
            )
        for rule in rules:
            if not isinstance(rule, Mapping):
                raise PolicyAlignmentError(
                    f"spec.{direction} содержит не-объект",
                    selector="<документ>",
                    scope="<unknown>",
                    reason="document_invalid",
                )
            for key in ("from", "to"):
                entries = rule.get(key) or []
                if isinstance(entries, Mapping):
                    entries = [entries]
                if not isinstance(entries, Sequence) or isinstance(entries, (str, bytes)):
                    continue
                for peer in entries:
                    if isinstance(peer, Mapping):
                        result.append((direction, peer))
    return result


def _ports(np_dict: Mapping[str, Any]) -> set[int]:
    """Все числовые порты из ingress/egress правил."""
    spec = np_dict.get("spec")
    if not isinstance(spec, Mapping):
        return set()
    ports: set[int] = set()
    for direction in ("ingress", "egress"):
        rules = spec.get(direction) or []
        if not isinstance(rules, Sequence) or isinstance(rules, (str, bytes)):
            continue
        for rule in rules:
            if not isinstance(rule, Mapping):
                continue
            entries = rule.get("ports") or []
            if not isinstance(entries, Sequence) or isinstance(entries, (str, bytes)):
                continue
            for port in entries:
                if isinstance(port, Mapping) and isinstance(port.get("port"), int) and not isinstance(port["port"], bool):
                    ports.add(port["port"])
    return ports


def _find_repo_root() -> Path:
    """Найти корень репозитория по признаку ``infra/k3s/base/plugins``.

    Не хардкодим число ``parents[N]``: модуль может лежать как в worktree, так и
    в установленном wheel. Если эталонные манифесты недоступны — отказ, а не
    «проверку пропустили».
    """
    for parent in Path(__file__).resolve().parents:
        if (parent / "infra" / "k3s" / "base" / "plugins").is_dir():
            return parent
    raise PolicyAlignmentError(
        "эталонные манифесты задачи 14 недоступны (не найден infra/k3s/base/plugins); "
        "применение NP запрещено",
        selector="<манифест>",
        scope="<unknown>",
        reason="manifest_missing",
    )


def as_built_manifest_path(scope: Any, *, repo_root: Path | None = None) -> Path:
    """Путь к эталонному манифесту задачи 14 для данного scope."""
    normalized = _normalize_scope(scope)
    root = repo_root or _find_repo_root()
    return root / AS_BUILT_MANIFEST_PATHS[normalized]


def load_as_built_manifest_text(scope: Any, *, repo_root: Path | None = None) -> str:
    """Прочитать эталонный манифест задачи 14 (для CI/тестов, без PyYAML).

    Читаем как текст: в рантайм-контейнере API нет ни ``infra/**``, ни PyYAML,
    поэтому сверка с ЖИВЫМ манифестом — это проверка уровня репозитория/CI.
    Runtime-барьер работает по замороженному снимку ``AS_BUILT_SELECTORS``.
    """
    path = as_built_manifest_path(scope, repo_root=repo_root)
    try:
        return path.read_text(encoding="utf-8")
    except OSError as exc:
        normalized = _normalize_scope(scope)
        raise PolicyAlignmentError(
            f"не удалось прочитать эталонный манифест {path}: {type(exc).__name__}; "
            "применение NP запрещено",
            selector="<манифест>",
            scope=normalized,
            reason="manifest_missing",
        ) from exc


def _clean_yaml_text(text: str) -> str:
    """Убрать ``#``-комментарии, чтобы НЕ ловить ``app: agentscale-postgres``
    из поясняющих строк манифеста (networkpolicy-plugin.yaml:13,102,152).
    """
    return "\n".join(line.split("#", 1)[0] for line in text.splitlines())


def _assert_manifest_matches(scope: str, manifest: Any) -> None:
    """Сверить ЖИВОЙ манифест задачи 14 с ``AS_BUILT_SELECTORS``.

    Дрейф манифеста (лейбл переименовали в k3s, а замок не обновили) ловится
    здесь. Поддерживаются: ``"auto"`` (загрузить из репозитория), ``Path``,
    сырой YAML-текст и уже разобранный ``Mapping``.
    """
    expected = _expected_selectors(scope)
    path_label = AS_BUILT_MANIFEST_PATHS[scope]

    if isinstance(manifest, Mapping):
        # Разобранный документ: сравниваем по тем же каноническим ключам.
        found = {
            _selector_key(peer["podSelector"]["matchLabels"])
            for _, peer in _peers(manifest)
            if isinstance(peer.get("podSelector"), Mapping)
            and isinstance(peer["podSelector"].get("matchLabels"), Mapping)
            and peer["podSelector"]["matchLabels"]
        }
        for name, selector in expected.items():
            if _selector_key(selector) not in found:
                raise PolicyAlignmentError(
                    f"в эталоне {path_label} отсутствует селектор {name!r} "
                    f"({_fmt_selector(selector)}); подтверждение: "
                    f"{AS_BUILT_PROVENANCE.get(name, AGENT_POD_GATEWAY_PROVENANCE)}",
                    selector=_fmt_selector(selector),
                    scope=scope,
                    reason="manifest_drift",
                )
        return

    if manifest == "auto":
        text = load_as_built_manifest_text(scope)
    elif isinstance(manifest, Path):
        try:
            text = manifest.read_text(encoding="utf-8")
        except OSError as exc:
            raise PolicyAlignmentError(
                f"не удалось прочитать эталон {manifest}: {type(exc).__name__}",
                selector="<манифест>",
                scope=scope,
                reason="manifest_missing",
            ) from exc
    elif isinstance(manifest, str):
        text = manifest
    else:
        raise PolicyAlignmentError(
            f"manifest должен быть 'auto', Path, текстом YAML или Mapping, получено {type(manifest).__name__}",
            selector="<манифест>",
            scope=scope,
            reason="manifest_invalid",
        )

    clean = _clean_yaml_text(text)
    for name, selector in expected.items():
        for key, value in selector.items():
            pattern = rf"(?m)^\s*{re.escape(key)}:\s*{re.escape(value)}\s*$"
            if re.search(pattern, clean) is None:
                raise PolicyAlignmentError(
                    f"в эталоне {path_label} нет строки '{key}: {value}' "
                    f"для селектора {name!r}; as-built лейбл изменился — "
                    f"обновите AS_BUILT_SELECTORS и манифест задачи 14",
                    selector=_fmt_selector(selector),
                    scope=scope,
                    reason="manifest_drift",
                )
    for name, selector in FORBIDDEN_SELECTORS.items():
        for key, value in selector.items():
            pattern = rf"(?m)^\s*{re.escape(key)}:\s*{re.escape(value)}\s*$"
            if re.search(pattern, clean) is not None:
                raise PolicyAlignmentError(
                    f"в эталоне {path_label} обнаружен ЗАПРЕЩЁННЫЙ селектор "
                    f"{name!r} ({_fmt_selector(selector)}); применение NP запрещено",
                    selector=_fmt_selector(selector),
                    scope=scope,
                    reason="manifest_drift",
                )


# ---------------------------------------------------------------------------
# ПУБЛИЧНЫЙ БАРЬЕР №1: селекторы NP
# ---------------------------------------------------------------------------


def assert_generated_policy_matches_as_built(
    np_dict: Any,
    scope: Any,
    *,
    manifest: Any | None = None,
    platform_namespace: str = PLATFORM_NAMESPACE,
) -> None:
    """ЯВНО ЗАПРЕТИТЬ применение NP при расхождении с as-built (fail-closed).

    Возвращает ``None`` ТОЛЬКО когда доказано полное совпадение:

    1. документ — корректная ``NetworkPolicy`` нужного имени и ``podSelector``;
    2. ``policyTypes`` содержит ``Ingress`` и ``Egress`` (иначе фаерволл не
       fail-closed);
    3. КАЖДЫЙ ``podSelector`` peer'а совпадает с ``AS_BUILT_SELECTORS``;
       выдуманные ``app: minio`` / ``app: kafka`` и ``app: agentscale-postgres``
       (prodavan) отклоняются с указанием конкретного селектора;
    4. КАЖДЫЙ ожидаемый селектор присутствует (пропущенное правило = дыра);
    5. ``namespaceSelector`` — строго по ``kubernetes.io/metadata.name``
       (лейбла ``name`` в ``namespace.yaml`` нет, §6.2);
    6. набор портов соответствует scope: у plugin есть 5432/6432/9000/9092/
       8080/53, у agent их НЕТ, у plugin НЕТ :8001 и :80/:443 и нет ``ipBlock``;
    7. если передан ``manifest`` — живой манифест задачи 14 тоже сверяется с
       эталоном (ловим дрейф лейблов в k3s).

    ``manifest=None`` — штатный runtime-режим: сверка идёт по замороженному
    снимку ``AS_BUILT_SELECTORS`` (в контейнере API нет ``infra/**``).
    ``manifest="auto"`` — режим CI/тестов: дополнительно читается файл задачи 14;
    если он недоступен — отказ.

    При ЛЮБОМ расхождении бросается ``PolicyAlignmentError``. Функция ничего не
    применяет и не возвращает «отчёт о проверке»: вызывающий ОБЯЗАН либо
    прекратить применение, либо использовать
    :func:`apply_generated_policy_guarded`.
    """
    normalized = _normalize_scope(scope)

    if not isinstance(np_dict, Mapping) or not np_dict:
        raise PolicyAlignmentError(
            "сгенерированная NP пуста или не является объектом; применение запрещено",
            selector="<документ>",
            scope=normalized,
            reason="document_invalid",
        )
    if np_dict.get("kind") != "NetworkPolicy" or np_dict.get("apiVersion") != "networking.k8s.io/v1":
        raise PolicyAlignmentError(
            "ожидалась networking.k8s.io/v1 NetworkPolicy; применение запрещено",
            selector="<документ>",
            scope=normalized,
            reason="document_invalid",
        )
    metadata = np_dict.get("metadata")
    if not isinstance(metadata, Mapping):
        raise PolicyAlignmentError(
            "у NP отсутствует metadata; применение запрещено",
            selector="<документ>",
            scope=normalized,
            reason="document_invalid",
        )
    expected_name = EXPECTED_NP_NAMES[normalized]
    if metadata.get("name") != expected_name:
        raise PolicyAlignmentError(
            f"имя NP {metadata.get('name')!r} не совпадает с эталоном "
            f"{expected_name!r} ({AS_BUILT_MANIFEST_PATHS[normalized]}); "
            "применение запрещено",
            selector="<metadata.name>",
            scope=normalized,
            reason="np_name_mismatch",
        )

    spec = np_dict.get("spec")
    if not isinstance(spec, Mapping):
        raise PolicyAlignmentError(
            "у NP отсутствует spec; применение запрещено",
            selector="<документ>",
            scope=normalized,
            reason="document_invalid",
        )
    policy_types = spec.get("policyTypes") or []
    if not isinstance(policy_types, Sequence) or set(policy_types) != {"Ingress", "Egress"}:
        raise PolicyAlignmentError(
            "policyTypes должен быть ровно [Ingress, Egress]: отсутствие Egress "
            "означает «разрешить весь исходящий трафик» (дыра в фаерволе)",
            selector="<spec.policyTypes>",
            scope=normalized,
            reason="policy_types_incomplete",
        )

    subject = spec.get("podSelector")
    subject_labels = subject.get("matchLabels") if isinstance(subject, Mapping) else None
    expected_subject = EXPECTED_SUBJECT_SELECTORS[normalized]
    if not isinstance(subject_labels, Mapping) or _selector_key(subject_labels) != _selector_key(expected_subject):
        raise PolicyAlignmentError(
            f"spec.podSelector {_fmt_selector(subject_labels or {})} не совпадает "
            f"с эталоном {_fmt_selector(expected_subject)}; NP может накрыть "
            "чужие поды (агентов / Gateway)",
            selector=_fmt_selector(subject_labels or {}),
            scope=normalized,
            reason="subject_selector_mismatch",
        )

    expected = _expected_selectors(normalized)
    allowed_keys = {_selector_key(sel): name for name, sel in expected.items()}
    forbidden = {
        _selector_key(sel): name for name, sel in FORBIDDEN_SELECTORS.items()
    }
    kube_dns_key = _selector_key(AS_BUILT_SELECTORS["kube-dns"])

    found: set[tuple[tuple[str, str], ...]] = set()
    for direction, peer in _peers(np_dict):
        # Прямой выход в интернет плагину запрещён (§6.3): только egress-proxy.
        if normalized == "plugin" and direction == "egress" and "ipBlock" in peer:
            raise PolicyAlignmentError(
                "plugin-NP содержит ipBlock (прямой egress в обход egress-proxy); "
                "применение запрещено (§6.3)",
                selector="<ipBlock>",
                scope=normalized,
                reason="direct_egress_forbidden",
            )

        pod_selector = peer.get("podSelector")
        if pod_selector is None:
            continue
        labels = pod_selector.get("matchLabels") if isinstance(pod_selector, Mapping) else None
        if not isinstance(labels, Mapping) or not labels:
            raise PolicyAlignmentError(
                "podSelector без matchLabels не может быть сверен с as-built; "
                "применение запрещено",
                selector="<podSelector>",
                scope=normalized,
                reason="selector_unreadable",
            )
        key = _selector_key(labels)
        readable = _fmt_selector(labels)

        if key in forbidden:
            raise PolicyAlignmentError(
                f"ЗАПРЕЩЁННЫЙ селектор {forbidden[key]!r} {readable} в {direction}-правиле: "
                f"это {'выдуманный лейбл раннего дизайна' if forbidden[key].startswith('legacy') else 'платформенный Postgres prodavan'}. "
                f"Эталон: {AS_BUILT_PROVENANCE.get('minio' if forbidden[key] == 'legacy-minio' else 'kafka', '')}. "
                "Политика не совпадёт ни с одним подом / открывает prodavan — применение ЗАПРЕЩЕНО",
                selector=readable,
                scope=normalized,
                reason="forbidden_selector",
            )
        if key not in allowed_keys:
            hint = ""
            if any(labels.get(k) == "minio" for k in labels) or any(labels.get(k) == "kafka" for k in labels):
                hint = " (похоже на выдуманный лейбл раннего дизайна §6.2)"
            raise PolicyAlignmentError(
                f"селектор {readable} в {direction}-правиле отсутствует в "
                f"AS_BUILT_SELECTORS для scope={normalized!r}{hint}; правило не "
                "совпадёт ни с одним реальным подом — применение ЗАПРЕЩЕНО",
                selector=readable,
                scope=normalized,
                reason="selector_mismatch",
            )
        found.add(key)

        # namespaceSelector: ТОЛЬКО kubernetes.io/metadata.name (§6.2).
        ns_selector = peer.get("namespaceSelector")
        ns_labels = ns_selector.get("matchLabels") if isinstance(ns_selector, Mapping) else None
        if not isinstance(ns_labels, Mapping) or set(ns_labels) != {"kubernetes.io/metadata.name"}:
            raise PolicyAlignmentError(
                f"у селектора {readable} неверный namespaceSelector "
                f"{_fmt_selector(ns_labels or {})}: нужен ровно "
                "'kubernetes.io/metadata.name' (лейбла 'name' в namespace.yaml нет)",
                selector=readable,
                scope=normalized,
                reason="namespace_selector_invalid",
            )
        expected_ns = KUBE_SYSTEM_NAMESPACE if key == kube_dns_key else platform_namespace
        actual_ns = ns_labels.get("kubernetes.io/metadata.name")
        if actual_ns != expected_ns:
            raise PolicyAlignmentError(
                f"у селектора {readable} namespace={actual_ns!r}, ожидался "
                f"{expected_ns!r}; применение запрещено",
                selector=readable,
                scope=normalized,
                reason="namespace_selector_invalid",
            )

    # Полнота: пропущенное правило — это тоже расхождение (тихая дыра).
    for name, selector in expected.items():
        if _selector_key(selector) not in found:
            raise PolicyAlignmentError(
                f"в сгенерированной NP отсутствует обязательный селектор {name!r} "
                f"({_fmt_selector(selector)}); подтверждение: "
                f"{AS_BUILT_PROVENANCE.get(name, AGENT_POD_GATEWAY_PROVENANCE)}. "
                "Правило потеряно — применение ЗАПРЕЩЕНО",
                selector=_fmt_selector(selector),
                scope=normalized,
                reason="selector_missing",
            )

    ports = _ports(np_dict)
    if normalized == "plugin":
        missing_ports = PLUGIN_REQUIRED_PORTS - ports
        if missing_ports:
            raise PolicyAlignmentError(
                f"в plugin-NP отсутствуют обязательные порты {sorted(missing_ports)} "
                "(§6.2: 5432 tenant-PG, 6432 pgbouncer, 9000 MinIO, 9092 Kafka, "
                "8080 gateway/proxy, 53 DNS); применение запрещено",
                selector="<ports>",
                scope=normalized,
                reason="plugin_port_missing",
            )
        forbidden_ports = PLUGIN_FORBIDDEN_PORTS & ports
        if forbidden_ports:
            raise PolicyAlignmentError(
                f"plugin-NP содержит запрещённые порты {sorted(forbidden_ports)}: "
                ":8001 — agent-surface (критерий №17), :80/:443 — прямой интернет "
                "в обход egress-proxy (§6.3); применение запрещено",
                selector="<ports>",
                scope=normalized,
                reason="plugin_port_forbidden",
            )
    else:
        forbidden_ports = AGENT_FORBIDDEN_PORTS & ports
        if forbidden_ports:
            raise PolicyAlignmentError(
                f"agent-NP содержит плагиновые порты {sorted(forbidden_ports)} "
                "(5432/6432/9000/9092/8080); §6.1 и networkpolicy-agent.yaml "
                "это ЗАПРЕЩАЮТ — применение запрещено",
                selector="<ports>",
                scope=normalized,
                reason="agent_port_forbidden",
            )

    # Дополнительная сверка с ЖИВЫМ манифестом задачи 14 (CI/тесты).
    if manifest is not None:
        _assert_manifest_matches(normalized, manifest)


def apply_generated_policy_guarded(
    np_dict: Any,
    scope: Any,
    apply: Callable[[Any], Any],
    *,
    manifest: Any | None = None,
    platform_namespace: str = PLATFORM_NAMESPACE,
) -> Any:
    """ЕДИНСТВЕННАЯ санкционированная точка ПРИМЕНЕНИЯ сгенерированной NP.

    Сначала :func:`assert_generated_policy_matches_as_built`, и ТОЛЬКО потом
    ``apply(np_dict)``. При любом расхождении ``apply`` НЕ вызывается — политика
    физически не доходит до Kubernetes. «Просто проверить и всё равно применить»
    здесь невозможно по конструкции.
    """
    assert_generated_policy_matches_as_built(
        np_dict,
        scope,
        manifest=manifest,
        platform_namespace=platform_namespace,
    )
    normalized = _normalize_scope(scope)
    if not callable(apply):
        raise PolicyAlignmentError(
            "apply должен быть callable; применение запрещено",
            selector="<apply>",
            scope=normalized,
            reason="apply_not_callable",
        )
    return apply(np_dict)


# ---------------------------------------------------------------------------
# ПУБЛИЧНЫЙ БАРЬЕР №2: источник egress-правил
# ---------------------------------------------------------------------------


def _as_fields(obj: Any) -> dict[str, Any]:
    """Прочитать поля claims/token без зависимости от ORM/Pydantic."""
    if obj is None:
        return {}
    if isinstance(obj, Mapping):
        return dict(obj)
    dumped = getattr(obj, "model_dump", None)
    if callable(dumped):
        value = dumped()
        if isinstance(value, Mapping):
            return dict(value)
    raw = getattr(obj, "__dict__", None)
    if isinstance(raw, Mapping):
        return dict(raw)
    return {}


def _flatten_chain(chain: Sequence[Any]) -> str:
    """Привести цепочку резолвинга к одной нижнерегистровой строке."""
    parts: list[str] = []
    for step in chain:
        if isinstance(step, Mapping):
            parts.append(" ".join(f"{k}={v}" for k, v in step.items()))
        else:
            parts.append(str(step))
    return " | ".join(parts).lower()


def assert_policy_source_is_grant(
    resolved: Any,
    *,
    chain: Sequence[Any] | None = None,
    claims: Any = None,
    token: Any = None,
    grant_id: str | None = None,
) -> None:
    """ЗАПРЕТИТЬ egress-правила из JWT; разрешить только ``CapabilityGrant``.

    Проверяется вся цепочка, а не только итоговый объект:

    * ``resolved`` — ``EgressPolicy``, Envoy-конфиг задачи 08 (``_meta``) или
      mapping. Если у него есть явный ``source``, он обязан быть ровно
      ``"CapabilityGrant"``; ``"JWT"`` / ``"PluginClaims"`` / ``"token"`` — отказ;
    * ``resolved.grant_id`` (или ``_meta.grant_id``) обязан быть непустым —
      это след резолвинга по ``grant_id``. Если передан ``grant_id``, значения
      должны совпасть;
    * в ``claims`` и ``token`` НЕ ДОЛЖНО быть ``egress``, ``allowed_hosts``,
      ``allowed_paths``, ``quotas``, ``max_egress_bytes`` (§4.2: их убрали из
      токена; наличие = попытка протащить политику мимо БД);
    * в ``chain`` НЕ ДОЛЖНО быть маркеров ``token.egress`` /
      ``claims.allowed_hosts`` и им подобных, но ДОЛЖЕН быть хотя бы один
      маркер резолвинга по grant (``grant_id`` / ``GrantStorePort.get_grant`` /
      ``CapabilityGrant.egress`` / ``resolve_policy`` …).

    Fail-closed: если цепочка не передана И объект не несёт явного
    ``source="CapabilityGrant"``, доказать источник нельзя -> отказ. Отсутствие
    ``grant_id`` -> отказ. Никакого «ну наверное, это из grant».

    Возвращает ``None`` только при подтверждённом источнике из БД.
    """
    scope = "egress-source"

    if resolved is None:
        raise PolicyAlignmentError(
            "результат резолвинга egress-политики отсутствует; применение запрещено",
            selector="<resolved>",
            scope=scope,
            reason="source_unknown",
        )

    if isinstance(resolved, Mapping):
        meta = resolved.get("_meta", resolved)
        meta = meta if isinstance(meta, Mapping) else {}
        source = meta.get("source")
        resolved_grant_id = meta.get("grant_id")
    else:
        source = getattr(resolved, "source", None)
        resolved_grant_id = getattr(resolved, "grant_id", None)

    if source is not None and str(source) != GRANT_SOURCE:
        raise PolicyAlignmentError(
            f"источник egress-правил {str(source)!r}, а не {GRANT_SOURCE!r}: "
            "правила построены из краткоживущего JWT/claims. Истечение TTL "
            "пересоберёт сетевые правила посреди запроса (критерий №22) — "
            "применение ЗАПРЕЩЕНО",
            selector=f"source: {source}",
            scope=scope,
            reason="source_is_jwt",
        )

    if not isinstance(resolved_grant_id, str) or not resolved_grant_id.strip():
        raise PolicyAlignmentError(
            "у egress-политики нет grant_id: она не резолвилась по "
            "CapabilityGrant в БД; применение запрещено",
            selector="<grant_id>",
            scope=scope,
            reason="grant_id_missing",
        )
    if grant_id is not None and grant_id.strip() != resolved_grant_id.strip():
        raise PolicyAlignmentError(
            f"grant_id из токена {grant_id!r} не совпадает с grant_id политики "
            f"{resolved_grant_id!r}; применение запрещено",
            selector="<grant_id>",
            scope=scope,
            reason="grant_id_mismatch",
        )

    for label, container in (("claims", claims), ("token", token)):
        fields = _as_fields(container)
        leaked = FORBIDDEN_JWT_EGRESS_FIELDS & set(fields)
        if leaked:
            raise PolicyAlignmentError(
                f"в {label} обнаружены запрещённые поля {sorted(leaked)}: "
                "egress-политика читается из JWT, а не из CapabilityGrant "
                "(§4.2, §6.1, §13.3). Применение ЗАПРЕЩЕНО",
                selector=f"{label}.{sorted(leaked)[0]}",
                scope=scope,
                reason="jwt_egress_field_present",
            )

    if chain is None:
        # Без цепочки источник доказываем ТОЛЬКО явным source + grant_id.
        if str(source) != GRANT_SOURCE:
            raise PolicyAlignmentError(
                "цепочка резолвинга не передана и объект не помечен "
                "source='CapabilityGrant': источник правил не доказан — "
                "применение запрещено (fail-closed)",
                selector="<chain>",
                scope=scope,
                reason="chain_missing",
            )
        return

    if isinstance(chain, (str, bytes)) or not isinstance(chain, Sequence) or not chain:
        raise PolicyAlignmentError(
            "цепочка резолвинга пуста или имеет неверный тип; источник правил "
            "не доказан — применение запрещено",
            selector="<chain>",
            scope=scope,
            reason="chain_missing",
        )

    flat = _flatten_chain(chain)
    for marker in FORBIDDEN_JWT_CHAIN_MARKERS:
        if marker in flat:
            raise PolicyAlignmentError(
                f"в цепочке резолвинга обнаружен JWT-источник '{marker}': "
                "egress-правила построены из полей токена, а не из "
                "CapabilityGrant по grant_id. Применение ЗАПРЕЩЕНО",
                selector=marker,
                scope=scope,
                reason="chain_jwt_marker",
            )
    if not any(marker in flat for marker in GRANT_CHAIN_MARKERS):
        raise PolicyAlignmentError(
            "в цепочке нет ни одного доказательства резолвинга по grant "
            "(grant_id / GrantStorePort.get_grant / CapabilityGrant.egress / "
            "resolve_policy); применение запрещено",
            selector="<chain>",
            scope=scope,
            reason="chain_grant_evidence_missing",
        )


__all__ = [
    "AGENT_FORBIDDEN_PORTS",
    "AGENT_POD_GATEWAY_PROVENANCE",
    "AGENT_POD_GATEWAY_SELECTOR",
    "AS_BUILT_MANIFEST_PATHS",
    "AS_BUILT_PROVENANCE",
    "AS_BUILT_SELECTORS",
    "EXPECTED_NP_NAMES",
    "EXPECTED_SUBJECT_SELECTORS",
    "FORBIDDEN_JWT_CHAIN_MARKERS",
    "FORBIDDEN_JWT_EGRESS_FIELDS",
    "FORBIDDEN_SELECTORS",
    "GRANT_CHAIN_MARKERS",
    "GRANT_SOURCE",
    "KUBE_SYSTEM_NAMESPACE",
    "PLATFORM_NAMESPACE",
    "PLUGIN_FORBIDDEN_PORTS",
    "PLUGIN_REQUIRED_PORTS",
    "POLICY_ALIGNMENT_ERROR_CODE",
    "PolicyAlignmentError",
    "apply_generated_policy_guarded",
    "as_built_manifest_path",
    "assert_generated_policy_matches_as_built",
    "assert_policy_source_is_grant",
    "load_as_built_manifest_text",
]
