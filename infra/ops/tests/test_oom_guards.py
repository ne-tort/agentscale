"""Защита узла от OOM закреплена в манифестах, а не в голове дежурного.

Инцидент 2026-10-23 (prod): нода ушла в глобальный OOM при 62 МБ свободной
памяти; следом apiserver перестал отвечать (запросы к Sandbox CR висели 19-22
минуты, kubelet не мог выполнить ListPodSandbox), и снаружи это выглядело как
«нода зависла по CPU». Причины: kubelet работал с дефолтами (единственный
арбитр — ядерный OOM-killer), системе не было ничего зарезервировано, а семь
подов Argo CD шли вообще без requests/limits — то есть BestEffort, которых
ядро убивает первыми.

Эти тесты фиксируют, что защита остаётся на месте: пороги выселения, резервы,
бюджеты namespace'ов и durable DNS-фикс после рестарта k3s.
"""

from __future__ import annotations

from pathlib import Path

import yaml

_INFRA = Path(__file__).resolve().parents[2]
_TERRAFORM = _INFRA / "terraform"
_K3S = _INFRA / "k3s"


def _kubelet_args() -> list[str]:
    tpl = _TERRAFORM / "modules/k3s-dev-host/templates/k3s-config.yaml.tpl"
    data = yaml.safe_load(tpl.read_text(encoding="utf-8"))
    return list(data["kubelet-arg"])


def test_kubelet_evicts_before_kernel_oom() -> None:
    """Выселение должно начинаться ДО того, как ядро пойдёт убивать."""
    args = " ".join(_kubelet_args())

    # Явный порог по доступной памяти — иначе работает только ядерный killer.
    assert "eviction-hard=" in args
    assert "memory.available<1200Mi" in args

    # Мягкий порог даёт сервисам время отдать кэш вместо мгновенного убийства.
    assert "eviction-soft=memory.available<1800Mi" in args
    assert "eviction-soft-grace-period=memory.available=90s" in args

    # Без minimum-reclaim выселение повторится на следующем же цикле.
    assert "eviction-minimum-reclaim=memory.available=256Mi" in args


def test_kubelet_reserves_memory_for_system() -> None:
    """k3s, containerd и page cache не должны конкурировать с подами."""
    args = " ".join(_kubelet_args())
    assert "system-reserved=memory=1024Mi" in args
    assert "kube-reserved=memory=512Mi" in args


def test_platform_guards_cover_namespaces_outside_gitops() -> None:
    """argocd и kube-system вне AppProject — их бюджеты живут в node-manifests."""
    path = _K3S / "node-manifests/platform-guards.yaml"
    docs = [d for d in yaml.safe_load_all(path.read_text(encoding="utf-8")) if d]

    limits = {d["metadata"]["namespace"] for d in docs if d["kind"] == "LimitRange"}
    assert limits == {"argocd", "kube-system", "agent-sandbox-system"}

    quotas = [d for d in docs if d["kind"] == "ResourceQuota"]
    assert [q["metadata"]["namespace"] for q in quotas] == ["argocd"]
    assert "limits.memory" in quotas[0]["spec"]["hard"]

    # Контейнер не должен уметь занять половину ноды одним махом.
    for doc in docs:
        if doc["kind"] != "LimitRange":
            continue
        for item in doc["spec"]["limits"]:
            assert item["max"]["memory"], f"нет max для {doc['metadata']['namespace']}"


def test_app_namespace_budget_is_wired_into_gitops() -> None:
    """Namespace приложения получает потолки через Argo, а не руками."""
    base_kust = (_K3S / "base/kustomization.yaml").read_text(encoding="utf-8")
    assert "guards/app-budget.yaml" in base_kust

    path = _K3S / "base/guards/app-budget.yaml"
    docs = [d for d in yaml.safe_load_all(path.read_text(encoding="utf-8")) if d]
    kinds = {d["kind"] for d in docs}
    assert kinds == {"LimitRange", "ResourceQuota"}

    # namespace подставляет NamespaceTransformer оверлея (dev/prod) —
    # жёстко прописанный namespace сломал бы второй контур.
    for doc in docs:
        assert "namespace" not in doc["metadata"], "namespace должен приходить из оверлея"

    quota = next(d for d in docs if d["kind"] == "ResourceQuota")
    # Потолок выше текущего потребления (замер 2026-10-23: limits 17.8 ГиБ),
    # иначе quota начнёт ломать обычные деплои.
    assert quota["spec"]["hard"]["limits.memory"] == "26Gi"
    assert quota["spec"]["hard"]["requests.memory"] == "12Gi"


def test_oom_guards_are_installed_by_terraform() -> None:
    """Файлы должны реально доезжать до ноды, иначе манифесты — мёртвый груз."""
    main_tf = (_TERRAFORM / "modules/k3s-dev-host/main.tf").read_text(encoding="utf-8")

    assert "templates/k3s-config.yaml.tpl" in main_tf
    assert "/etc/rancher/k3s/config.yaml" in main_tf
    assert "node-manifests/platform-guards.yaml" in main_tf
    # k3s применяет addon-манифесты сам — файл должен лежать именно здесь.
    assert "server/manifests/prodavan-platform-guards.yaml" in main_tf


def test_appproject_allows_guard_kinds() -> None:
    """AppProject обязан разрешать LimitRange/ResourceQuota.

    Регресс 2026-10-23: guards добавили в base, но whitelist проекта их не
    разрешал. Argo считает такую задачу невалидной и роняет синк ЦЕЛИКОМ —
    `one or more synchronization tasks are not valid`, ревизия остаётся
    OutOfSync, и вместе с guard'ами не применяется ничего, включая обновление
    образов. То есть защита от OOM заблокировала деплой.
    """
    project = (
        _INFRA / "argocd/apps/00-project.yaml"
    ).read_text(encoding="utf-8")
    data = yaml.safe_load(project)
    allowed = {
        (item.get("group", ""), item["kind"])
        for item in data["spec"]["namespaceResourceWhitelist"]
    }

    assert ("", "LimitRange") in allowed
    assert ("", "ResourceQuota") in allowed

    # Каждый guard-объект из base должен быть разрешён проектом — иначе синк
    # снова упадёт на первом же непринятом kind.
    guards = _K3S / "base/guards/app-budget.yaml"
    for doc in yaml.safe_load_all(guards.read_text(encoding="utf-8")):
        if not doc:
            continue
        key = (doc.get("apiVersion", "").split("/")[0] if "/" in doc.get("apiVersion", "") else "", doc["kind"])
        assert key in allowed, f"{doc['kind']} не разрешён в AppProject agentscale"


def test_dns_fix_survives_k3s_restart() -> None:
    """k3s перезаписывает CoreDNS при каждом старте — фикс должен навязываться.

    2026-10-22 и 2026-10-23: после рестарта сервера из подов не резолвилось ни
    одно внешнее имя (CoreDNS форвардил на мёртвый nameserver из хостового
    resolv.conf), агент не мог дойти до провайдера ИИ.
    """
    heal = (
        _TERRAFORM / "modules/k3s-dev-host/templates/post-k3s-heal.sh.tpl"
    ).read_text(encoding="utf-8")

    assert "ensure_coredns_forward()" in heal
    # функция должна вызываться, а не просто существовать
    assert "\nensure_coredns_forward\n" in heal
    # и не трогать dev, где хостовый резолвер рабочий
    assert '[ -z "$DNS_UPSTREAMS" ] && return 0' in heal

    # Регресс-страховка: данные в python-блок нельзя отдавать через stdin —
    # `python3 - <<'PY'` уже занимает stdin программой, pipe в него не доходит,
    # sys.stdin.read() возвращает пустоту и фикс молча не применяется
    # (ровно так функция и не сработала в первом заходе). Проверяем код, а не
    # комментарии: пояснение выше само упоминает stdin.
    code_lines = [
        line for line in heal.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    code = "\n".join(code_lines)
    assert "sys.stdin.read()" not in code, "Corefile нельзя читать из stdin"
    assert "open(sys.argv[2]" in code, "Corefile должен читаться из файла"

    prod_env = (_TERRAFORM / "environments/prod/main.tf").read_text(encoding="utf-8")
    assert "dns_upstreams" in prod_env
    assert "172.19.0.2 8.8.8.8 1.1.1.1" in prod_env