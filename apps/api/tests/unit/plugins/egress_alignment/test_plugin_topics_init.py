"""Тесты Job `plugin-topics-init.yaml` (НАПИСАНЫ, НЕ ЗАПУСКАЛИСЬ).

Требования задачи:
  * ровно 2 топика: `agentscale.plugin.events` и `agentscale.plugin.events.dlq`;
  * `-p 1 -r 1` (RF=1, single-node Redpanda);
  * `cleanup.policy=delete`, `retention.ms=604800000`;
  * `.dlq` ровно ОДИН раз (не `.dlq.dlq`);
  * проверка существования ПОСЛЕ create + `exit 1` (fail-closed);
  * Sync-хук wave 5, kind `Job` в whitelist `00-project.yaml`.

PyYAML НЕ требуется: разбираем block-скаляр `command: ... - |` текстом, чтобы
тест не зависел от dev-зависимостей (в `apps/api/pyproject.toml` pyyaml нет).
"""

from __future__ import annotations

import re

from .helpers import PLATFORM_JOB, PROJECT_WHITELIST, REPO_ROOT


def _script() -> str:
    """Достать shell-скрипт из `command: [- /bin/sh, - -c, - |]` без PyYAML."""
    text = PLATFORM_JOB.read_text(encoding="utf-8")
    lines = text.splitlines()
    marker = next(i for i, line in enumerate(lines) if line.strip() == "- |")
    body: list[str] = []
    for line in lines[marker + 1 :]:
        if line.strip() and not line.startswith(" " * 14):
            break
        body.append(line[14:] if line.startswith(" " * 14) else "")
    return "\n".join(body)


def _yaml_text() -> str:
    return PLATFORM_JOB.read_text(encoding="utf-8")


SCRIPT = _script()
YAML = _yaml_text()


# ------------------------------------------------------------------ 2 топика


def test_job_creates_exactly_two_topics() -> None:
    """Ровно 2 вызова `rpk topic create`, ни одного лишнего топика."""
    creates = re.findall(r"^\s*rpk topic create\s+(\S+)", SCRIPT, re.MULTILINE)
    assert creates == ["agentscale.plugin.events", "agentscale.plugin.events.dlq"]


def test_topic_names_match_events_topics_py_contract() -> None:
    """Имена согласованы с `events/topics.py:35,39` (единый источник истины)."""
    topics_py = (
        REPO_ROOT
        / "apps"
        / "api"
        / "src"
        / "prodavan"
        / "application"
        / "plugins"
        / "events"
        / "topics.py"
    ).read_text(encoding="utf-8")
    assert 'PLUGIN_EVENTS_TOPIC = "agentscale.plugin.events"' in topics_py
    assert 'PLUGIN_DLQ_SUFFIX = ".dlq"' in topics_py
    assert "agentscale.plugin.events" in SCRIPT
    assert "agentscale.plugin.events.dlq" in SCRIPT


def test_dlq_suffix_appears_once_not_twice() -> None:
    """`.dlq.dlq` запрещён: DLQ-сообщение не должно уйти в DLQ повторно."""
    assert "agentscale.plugin.events.dlq.dlq" not in SCRIPT
    assert "agentscale.plugin.events.dlq.dlq" not in YAML
    # Ровно одно имя с суффиксом .dlq в командах create.
    dlq_creates = re.findall(r"rpk topic create agentscale\.plugin\.events(\.dlq)+", SCRIPT)
    assert dlq_creates == [".dlq"]
    # И ровно один раз в списке alter-config / проверки.
    for line in SCRIPT.splitlines():
        if "agentscale.plugin.events" in line and "dlq" in line:
            assert line.count(".dlq") == 1


def test_job_does_not_touch_the_nine_platform_topics() -> None:
    """9 платформенных топиков ведёт kafka.yaml; новый Job их НЕ создаёт."""
    for platform_topic in (
        "agentscale.platform.events",
        "agentscale.project.triggers",
        "agentscale.ops.health",
        "agentscale.auth.commands",
        "agentscale.auth.events",
        "agentscale.relation.events",
        "agentscale.metrics.events",
        "agentscale.document.events",
        "agentscale.search.events",
    ):
        assert platform_topic not in SCRIPT


def test_kafka_yaml_really_does_not_create_plugin_topics() -> None:
    """Подтверждение проблемы: init-Job kafka.yaml не создаёт plugin-топики."""
    kafka = (PLATFORM_JOB.parent / "kafka.yaml").read_text(encoding="utf-8")
    assert "agentscale.plugin.events" not in kafka
    assert "auto_create_topics_enabled false" in kafka
    # Ровно 9 create в эталонном Job (ни одного лишнего).
    creates = re.findall(r"^\s*rpk topic create\s+(\S+)", kafka, re.MULTILINE)
    assert len(creates) == 9


# ------------------------------------------------------------------- RF / ret


def test_both_topics_use_p1_r1() -> None:
    """`-p 1 -r 1` для КАЖДОГО топика (topics.py:41-45, RF=1 single-node)."""
    commands = re.findall(r"^\s*rpk topic create\s+(\S+)\s+(.+)$", SCRIPT, re.MULTILINE)
    assert len(commands) == 2
    for topic, args in commands:
        assert "-p 1" in args, topic
        assert "-r 1" in args, topic
        assert "-X brokers=$BROKERS" in args, topic
        assert "-r 3" not in args and "-r 2" not in args


def test_cleanup_policy_and_retention_are_set_for_both() -> None:
    """`cleanup.policy=delete`, `retention.ms=604800000`, min.insync.replicas=1."""
    assert "cleanup.policy=delete" in SCRIPT
    assert "retention.ms=604800000" in SCRIPT
    assert "min.insync.replicas=1" in SCRIPT
    # alter-config применяется к обоим топикам (цикл `for t in ...`).
    assert re.search(
        r"for t in agentscale\.plugin\.events agentscale\.plugin\.events\.dlq; do", SCRIPT
    )
    assert SCRIPT.count("rpk topic alter-config") == 1
    kafka = (PLATFORM_JOB.parent / "kafka.yaml").read_text(encoding="utf-8")
    assert "log_retention_ms 604800000" in kafka  # согласовано с платформой


# --------------------------------------------------------------- Fail-closed


def test_job_verifies_topic_existence_after_create() -> None:
    """Повторная проверка ПОСЛЕ create + `exit 1`, а не «надейся на auto-create»."""
    # `rpk topic list` печатает ТАБЛИЦУ (`NAME PARTITIONS REPLICAS`), поэтому
    # проверяем первый столбец через awk и точное совпадение имени `grep -Fxq`.
    # Прямой `grep -qx "$t"` по всему выводу промахивался бы всегда и Job вечно
    # уходил бы в CrashLoopBackOff при уже созданных топиках.
    assert "topics=$(rpk topic list -X brokers=$BROKERS | awk 'NR > 1 { print $1 }')" in SCRIPT
    assert "grep -Fxq \"$t\"" in SCRIPT
    assert "exit 1" in SCRIPT
    assert "отсутствует после create" in SCRIPT
    # Проверка идёт ПОСЛЕ всех create (порядок имеет значение).
    last_create = SCRIPT.rfind("rpk topic create")
    first_check = SCRIPT.find("grep -Fxq")
    assert first_check > last_create


def test_job_fails_on_exactly_two_topics_mismatch() -> None:
    """Ровно 2 имени под шаблоном `(\\.dlq)?`, иначе fail."""
    assert "matched=$(printf '%s\\n' \"$topics\"" in SCRIPT
    assert "[ \"$matched\" -ne 2 ]" in SCRIPT
    # Шаблон не допускает `.dlq.dlq` (только один необязательный суффикс).
    assert r"^agentscale\.plugin\.events(\.dlq)?$" in SCRIPT


def test_job_is_strict_shell_with_bounded_waits() -> None:
    """`set -eu`, ожидание Kafka API и Admin API с ограничением попыток."""
    assert "set -eu" in SCRIPT
    assert "rpk cluster info -X brokers=$BROKERS" in SCRIPT
    assert "admin.hosts=$ADMIN" in SCRIPT
    assert SCRIPT.count("-gt 60") == 2  # оба ожидания ограничены
    # `|| true` допустим на create (повторный синк) и на `grep -Ec` (0 совпадений
    # не должен рвать set -e раньше явной проверки matched).
    for line in SCRIPT.splitlines():
        if "|| true" in line:
            assert "rpk topic create" in line or "grep -Ec" in line
    # Ни одного `|| true` на alter-config / проверке существования.
    for line in SCRIPT.splitlines():
        if "rpk topic alter-config" in line or "grep -Fxq" in line:
            assert "|| true" not in line


# ------------------------------------------------------- Хук / whitelist / k8s


def test_job_is_sync_hook_wave_5_like_kafka_init() -> None:
    """Тот же Sync-хук и wave 5, что у `agentscale-kafka-init`."""
    assert "argocd.argoproj.io/hook: Sync" in YAML
    assert 'argocd.argoproj.io/sync-wave: "5"' in YAML
    assert "argocd.argoproj.io/hook-delete-policy: BeforeHookCreation,HookSucceeded" in YAML
    kafka = (PLATFORM_JOB.parent / "kafka.yaml").read_text(encoding="utf-8")
    assert 'argocd.argoproj.io/sync-wave: "5"' in kafka
    assert "argocd.argoproj.io/hook: Sync" in kafka


def test_job_kind_is_in_appproject_whitelist() -> None:
    """`batch/Job` разрешён `00-project.yaml:62-63`; новых kinds нет."""
    assert "kind: Job" in YAML
    whitelist = PROJECT_WHITELIST.read_text(encoding="utf-8")
    assert re.search(r"- group: batch\s+kind: Job", whitelist)
    # Никаких CRD/неизвестных kinds в новом манифесте.
    kinds = re.findall(r"^kind:\s*(\S+)", YAML, re.MULTILINE)
    assert kinds == ["Job"]


def test_job_uses_same_image_and_no_namespace_hardcode() -> None:
    """Образ как в kafka.yaml:185; namespace подставляет оверлей."""
    assert "ghcr.io/ne-tort/redpanda:v24.2.4" in YAML
    kafka = (PLATFORM_JOB.parent / "kafka.yaml").read_text(encoding="utf-8")
    assert "ghcr.io/ne-tort/redpanda:v24.2.4" in kafka
    assert "- name: ghcr-pull" in YAML
    assert not re.search(r"^\s*namespace:\s*agentscale", YAML, re.MULTILINE)


def test_job_is_wired_into_platform_kustomization() -> None:
    """Родитель подключил файл: без этого манифест не попадёт в кластер."""
    kustomization = (PLATFORM_JOB.parent / "kustomization.yaml").read_text(encoding="utf-8")
    assert "- plugin-topics-init.yaml" in kustomization
