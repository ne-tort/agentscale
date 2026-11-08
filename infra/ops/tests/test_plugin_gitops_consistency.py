"""GitOps-консистентность плагинового контура (статический аудит рендеров).

В отличие от соседних тестов (которые читают исходные YAML как текст/данные),
этот тест ПАРСИТ РЕЗУЛЬТАТ локального ``kubectl kustomize`` для двух оверлеев:

* ``infra/k3s/overlays/dev``          — Argo Application ``agentscale-dev``
* ``infra/k3s/overlays/dev/plugins``  — Argo Application ``plugin-platform``

Зачем: ``- plugins`` был убран из ``infra/k3s/base/kustomization.yaml``, иначе
два Application владели бы одними и теми же объектами и перетирали бы друг
друга при ``selfHeal``. Тест пинит (1) отсутствие пересечений kind/name и
(2) наличие ВСЕХ Secret, на которые ссылаются Deployment'ы, — две ошибки,
которые всплывают сразу после Argo sync (pod в ``CreateContainerConfigError``).

``kubectl`` используется ТОЛЬКО как локальный рендерер: без kubeconfig, без
кластера, без apply/argocd. Если бинарника нет — все проверки skip.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]
DEV_OVERLAY = REPO_ROOT / "infra" / "k3s" / "overlays" / "dev"
PLUGINS_OVERLAY = DEV_OVERLAY / "plugins"

WORKLOAD_KINDS = {"Deployment", "StatefulSet", "DaemonSet", "ReplicaSet", "Job", "Pod"}


def _kustomize(path: Path) -> list[dict]:
    """Локальный рендер оверлея; fail-closed на любом ненулевом exit code."""
    # Декодируем ЯВНО в UTF-8: на Windows text=True унаследовал бы cp1251 и
    # упал на кириллице в комментариях/аннотациях манифестов.
    proc = subprocess.run(
        ["kubectl", "kustomize", str(path)],
        cwd=str(REPO_ROOT),
        capture_output=True,
        check=False,
    )
    assert proc.returncode == 0, (
        f"kubectl kustomize {path} упал с exit {proc.returncode}:\n"
        f"{proc.stderr.decode('utf-8', errors='replace').strip()}"
    )
    rendered = proc.stdout.decode("utf-8")
    docs = [d for d in yaml.safe_load_all(rendered) if d]
    assert docs, f"kubectl kustomize {path} вернул пустой рендер"
    return docs


@pytest.fixture(scope="module")
def renders() -> dict[str, list[dict]]:
    """Один запуск kustomize на оба оверлея за прогон тестов."""
    return {
        "dev": _kustomize(DEV_OVERLAY),
        "plugins": _kustomize(PLUGINS_OVERLAY),
    }


def _identity(doc: dict) -> tuple[str, str | None, str | None]:
    meta = doc.get("metadata") or {}
    return (str(doc.get("kind")), meta.get("namespace"), meta.get("name"))


def _pod_spec(doc: dict) -> dict | None:
    """spec контейнеров workload'а (у Job — spec.template.spec)."""
    if doc.get("kind") not in WORKLOAD_KINDS:
        return None
    spec = doc.get("spec") or {}
    if doc.get("kind") == "Pod":
        return spec
    return (spec.get("template") or {}).get("spec")


def _secret_refs(doc: dict) -> list[tuple[str, str, bool]]:
    """(workload, имя Secret, optional) для envFrom/secretKeyRef/volume.secret."""
    name = (doc.get("metadata") or {}).get("name") or "<unnamed>"
    spec = _pod_spec(doc) or {}
    refs: list[tuple[str, str, bool]] = []
    containers = list(spec.get("containers") or []) + list(
        spec.get("initContainers") or []
    )
    for container in containers:
        for env_from in container.get("envFrom") or []:
            ref = env_from.get("secretRef")
            if ref and ref.get("name"):
                refs.append((name, ref["name"], bool(ref.get("optional"))))
        for env in container.get("env") or []:
            ref = (env.get("valueFrom") or {}).get("secretKeyRef")
            if ref and ref.get("name"):
                refs.append((name, ref["name"], bool(ref.get("optional"))))
    for volume in spec.get("volumes") or []:
        ref = volume.get("secret")
        if ref and ref.get("secretName"):
            refs.append((name, ref["secretName"], bool(ref.get("optional"))))
        for source in (volume.get("projected") or {}).get("sources") or []:
            ref = source.get("secret")
            if ref and ref.get("name"):
                refs.append((name, ref["name"], bool(ref.get("optional"))))
    return refs


@pytest.mark.skipif(shutil.which("kubectl") is None, reason="kubectl не установлен")
def test_rendered_overlays_have_no_shared_objects(renders: dict[str, list[dict]]) -> None:
    """Один объект = один владелец: пересечений kind/name быть не должно."""
    dev = {_identity(d) for d in renders["dev"]}
    plugins = {_identity(d) for d in renders["plugins"]}

    intersection = dev & plugins
    assert not intersection, (
        "двойное владение объектами между agentscale-dev и plugin-platform: "
        f"{sorted(intersection)}"
    )
    # Имя без kind/ns тоже не должно совпадать: такие объекты легко превратить
    # в дубли при следующей правке kustomization.yaml.
    dev_names = {(d.get("metadata") or {}).get("name") for d in renders["dev"]}
    plugin_names = {
        (d.get("metadata") or {}).get("name") for d in renders["plugins"]
    }
    assert not (dev_names & plugin_names), (
        "совпадают имена объектов в двух оверлеях: "
        f"{sorted(dev_names & plugin_names)}"
    )


@pytest.mark.skipif(shutil.which("kubectl") is None, reason="kubectl не установлен")
def test_all_secrets_referenced_by_deployments_exist(renders: dict[str, list[dict]]) -> None:
    """Каждый Secret, на который ссылается Deployment, есть в рендере или optional."""
    docs = renders["dev"] + renders["plugins"]
    existing = {
        (d["metadata"].get("namespace"), d["metadata"].get("name"))
        for d in docs
        if d.get("kind") == "Secret"
    }
    # Секреты, создаваемые контроллерами/операторами вне Git, допустимы только
    # с явным optional: true (иначе pod упадёт в CreateContainerConfigError).
    missing: list[str] = []
    for doc in docs:
        if doc.get("kind") != "Deployment":
            continue
        namespace = (doc.get("metadata") or {}).get("namespace")
        for workload, secret_name, optional in _secret_refs(doc):
            if (namespace, secret_name) not in existing and not optional:
                missing.append(
                    f"Deployment/{workload} -> Secret/{secret_name} "
                    f"(ns={namespace}, optional=false)"
                )
    assert not missing, "неразрешимые ссылки на Secret:\n" + "\n".join(missing)
