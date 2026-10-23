"""Пины образа agent-runtime должны совпадать во ВСЕХ манифестах.

Инцидент 2026-10-22: `web.fetch` агента упирался в Qrator (401), хотя searchnet
эту же страницу отдаёт. Причина — не код, а образ: `sandboxtemplate.yaml` и
`configmap.yaml` пинили `929b6768cba2` (сборка от 9 окт, `web.fetch` там делает
голый fetch на сайт), а `patch-dev.yaml` уже был обновлён на сборку с
прокси-веткой. Правку сделали в ОДНОМ месте из четырёх, и поды продолжали
ходить напрямую.

Тест превращает «не забыть остальные три файла» в проверяемое условие: пины
обязаны быть одинаковыми и версионированными (не `:latest`) — иначе под
молча едет на старом бинаре, а симптом выглядит как баг приложения.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

#: tests/ → ops/ → infra/ ; манифесты лежат в infra/k3s.
_INFRA = Path(__file__).resolve().parents[2] / "k3s"

#: `ghcr.io/ne-tort/prodavan-agent-runtime:<tag>`; тег — первые 12 символов sha
#: коммита prodavan-claw (immutable, чтобы под не «переезжал» под ногами).
_PIN_RE = re.compile(
    r"ghcr\.io/ne-tort/prodavan-agent-runtime:([A-Za-z0-9._-]+)"
)
_SHA12_RE = re.compile(r"^[0-9a-f]{12}$")


def _scan_pins() -> dict[Path, list[str]]:
    """Файл → все теги agent-runtime, которые в нём встречаются."""
    found: dict[Path, list[str]] = {}
    if not _INFRA.is_dir():  # pragma: no cover - инфра всегда рядом в репо
        return found
    for path in _INFRA.rglob("*.yaml"):
        text = path.read_text(encoding="utf-8", errors="replace")
        tags = _PIN_RE.findall(text)
        if tags:
            found[path] = tags
    return found


def test_pins_exist() -> None:
    """Пины не должны исчезнуть из манифестов (иначе тест ниже стал бы пустым)."""
    pins = _scan_pins()
    assert pins, "в infra/k3s не найдено ни одного пина prodavan-agent-runtime"


def test_all_manifests_pin_the_same_image() -> None:
    pins = _scan_pins()
    distinct = {tag for tags in pins.values() for tag in tags}
    versions = sorted(distinct)
    assert len(versions) == 1, (
        "разные теги prodavan-agent-runtime в манифестах — поды будут на разных "
        f"сборках: {versions}\n"
        + "\n".join(f"  {p}: {sorted(set(t))}" for p, t in sorted(pins.items()) if len(set(t)) or True)
    )


def test_pin_is_immutable_sha_tag() -> None:
    """`:latest` и прочие плавающие теги запрещены.

    Плавающий тег + `imagePullPolicy: IfNotPresent` = под навсегда остаётся на
    локально закэшированном слое (ровно то, что случилось с searchnet).
    """
    bad: list[tuple[Path, str]] = []
    for path, tags in _scan_pins().items():
        for tag in tags:
            if not _SHA12_RE.match(tag):
                bad.append((path, tag))
    assert not bad, f"плавающий/нестандартный тег agent-runtime: {bad}"


def test_pin_has_provenance_comment() -> None:
    """Рядом с пином должен быть комментарий с версией claw.

    Без него невозможно понять, какая ревизия claw в образе, и «обновить пин»
    превращается в угадывание.
    """
    missing: list[Path] = []
    for path in _scan_pins():
        text = path.read_text(encoding="utf-8", errors="replace")
        if "prodavan-claw@" not in text:
            missing.append(path)
    assert not missing, (
        "у пина agent-runtime нет комментария `prodavan-claw@<sha>` "
        f"(какая сборка claw в образе): {missing}"
    )


@pytest.mark.parametrize(
    "rel",
    [
        "base/agentscale-api/configmap.yaml",
        "base/agentscale-sandboxes-ks/sandboxtemplate.yaml",
        "overlays/dev/patch-dev.yaml",
        "overlays/dev/sandboxes/probe-pod.yaml",
    ],
)
def test_known_pin_sites_are_pinned(rel: str) -> None:
    """Четыре известных места пина обязаны содержать тег.

    Список фиксируем явно: манифест, из которого пин выпал, отдаёт под на
    произвольную/дефолтную сборку.
    """
    path = _INFRA / rel
    assert path.is_file(), f"нет манифеста {rel}"
    tags = _PIN_RE.findall(path.read_text(encoding="utf-8", errors="replace"))
    assert tags, f"{rel}: нет пина prodavan-agent-runtime"


# ── searchnet: тот же класс ошибки, что у agent-runtime ──────────────────────

#: `ghcr.io/ne-tort/searchnet:<tag>`.
_SEARCHNET_RE = re.compile(r"ghcr\.io/ne-tort/searchnet:([A-Za-z0-9._-]+)")


def test_searchnet_is_pinned_not_floating() -> None:
    """searchnet не должен ехать на `:latest`.

    Инцидент 2026-10-22: образ пересобрали с фиксом маршрутов, но `:latest` в
    GHCR обновился, а под продолжал работать на старом локальном слое — ничего
    в манифесте не менялось, значит Argo не перекатывал Deployment. Снаружи это
    выглядело как «фикс не работает».

    Пин на immutable-тег делает перекат декларативным: обновили тег → Argo
    увидел diff → новый под тянет ровно ту сборку. `imagePullPolicy: Always`
    этого НЕ заменяет: он гарантирует свежесть тега, но не перезапуск.
    """
    pins = _SEARCHNET_RE.findall(
        (_INFRA / "base/platform/searchnet.yaml").read_text(encoding="utf-8")
    )
    assert pins, "searchnet.yaml: нет пина образа searchnet"
    floating = [t for t in pins if not _SHA12_RE.match(t)]
    assert not floating, (
        f"searchnet на плавающем теге {floating}: пересборка образа не приведёт к "
        "перекату пода (нет diff в манифесте)"
    )


def test_searchnet_pin_has_provenance_comment() -> None:
    """Рядом с пином — какой коммит searchnet в образе."""
    text = (_INFRA / "base/platform/searchnet.yaml").read_text(encoding="utf-8")
    assert "searchnet@" in text, (
        "у пина searchnet нет комментария `searchnet@<sha>` — непонятно, какая "
        "ревизия исходников внутри"
    )
