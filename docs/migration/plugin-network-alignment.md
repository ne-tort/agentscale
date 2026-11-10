# Сверка сетевой политики плагинов с as-built (network alignment)

> **HISTORICAL REPORT / NOT CURRENT ACCEPTANCE.** Содержимое относится к ранней
> волне и не переподтверждено на audited HEAD `6ee720dd`. Любые «реализовано»,
> `[x]`, `acceptance ✓`, «эталон» и прошлые test counts — только historical
> claims. Актуальный статус: [STOP-SHIP](plugin-migration-status.md) и
> [live-only acceptance map](plugin-acceptance-map.md). Написанный код ≠ live.

**Статус:** реализовано, тесты написаны но НЕ запускались (verification freeze,
`docs/migration/plugin-migration-plan.md` §0.2, §9 дизайна v4).
**Источник архитектуры:** [`docs/ARCHITECTURE/plugin-architecture-complete.md`](../ARCHITECTURE/plugin-architecture-complete.md)
v4, §6.1, §6.2, §6.3, §7.3, §11 кр.20/22, §13.3.

---

## 0. Суть проблемы и что сделано

Задача 14 (`infra/k3s/base/plugins/**`) выявила расхождение между ранним дизайном
и реальными манифестами k3s: в §6.2 v1/v3 фигурировали выдуманные селекторы
`app: minio` / `app: kafka`, тогда как поды помечены
`app.kubernetes.io/name: agentscale-minio` / `agentscale-kafka`. NP с выдуманными
лейблами применяется **без ошибки**, но не совпадает ни с одним подом — правила
молча не работают. Плюс egress-политика обязана читаться из `CapabilityGrant` в БД,
а не из краткоживущего JWT (§6.1, §6.3, критерий №22). Плюс init-Job Kafka
создаёт 9 топиков, но не `agentscale.plugin.events` и `.dlq`, а
`auto_create_topics_enabled=false` (§7.3, критерий №20).

**Важное уточнение по факту:** на момент сверки задача 08 УЖЕ использует as-built
лейблы — `apps/api/src/prodavan/application/plugins/egress/generator.py:41-48`,
и `apps/api/tests/unit/plugins/egress/test_generator.py:147-160` это проверяет.
Переделывать `generator.py` не потребовалось (и он НЕ тронут: файл в чужой зоне,
задача 08 ещё работает). Поэтому ценность этой задачи — **не исправление, а
fail-closed регрессионный замок**: модуль
[`alignment.py`](../../apps/api/src/prodavan/application/plugins/egress/alignment.py)
ЯВНО ЗАПРЕЩАЕТ применение политики при любом будущем «уезде» к дизайну дословно.

| Файл | Роль |
|---|---|
| `apps/api/src/prodavan/application/plugins/egress/alignment.py` | **новый**, единственный источник истины лейблов `AS_BUILT_SELECTORS` + 2 барьера |
| `infra/k3s/base/platform/plugin-topics-init.yaml` | **новый**, Sync-хук wave 5: 2 топика плагинов |
| `apps/api/tests/unit/plugins/egress_alignment/**` | **новые** тесты (НЕ запускались) |
| `docs/migration/plugin-network-alignment.md` | этот документ |

Существующие файлы `egress/**`, `kafka.yaml`, `kustomization*`,
`docs/ARCHITECTURE/**` — **не редактировались**.

---

## 1. Таблица селекторов

Столбцы: логический селектор → реальный as-built лейбл → где подтверждён
(`файл:строка`) → что было в раннем дизайне → что делает
`assert_generated_policy_matches_as_built`.

| Селектор | Реальный лейбл (as-built) | Где подтверждён | Ранний дизайн | Действие `assert_generated_policy_matches_as_built` |
|---|---|---|---|---|
| `minio` | `app.kubernetes.io/name: agentscale-minio` | `infra/k3s/base/platform/minio.yaml:38,50,67,71`; `infra/k3s/base/plugins/networkpolicy-plugin.yaml:134` | `app: minio` | Требует ТОЧНОГО ключа `app.kubernetes.io/name`; `{"app": "minio"}` → `PolicyAlignmentError(reason="forbidden_selector", selector="{app: minio}")` и ЗАПРЕТ применения. Проверяет `namespaceSelector=agentscale-dev`, порт `9000`. |
| `kafka` (Redpanda) | `app.kubernetes.io/name: agentscale-kafka` | `infra/k3s/base/platform/kafka.yaml:21,35,55,59`; `infra/k3s/base/plugins/networkpolicy-plugin.yaml:147` | `app: kafka` | Требует ТОЧНОГО ключа `app.kubernetes.io/name`; `{"app": "kafka"}` → `PolicyAlignmentError(reason="forbidden_selector")`, ЗАПРЕТ. Проверяет `namespaceSelector=agentscale-dev`, порт `9092`. |
| `tenant-postgres` | `app: tenant-postgres` | `infra/k3s/base/plugins/tenant-postgres.yaml:275,279`; `infra/k3s/base/plugins/networkpolicy-plugin.yaml:109` | `app: tenant-postgres` (совпадает) | Требует присутствия селектора и порта `5432`; `app: agentscale-postgres` (`prodavan`) → `PolicyAlignmentError(reason="forbidden_selector")`. |
| `pgbouncer` | `app: pgbouncer` | `infra/k3s/base/plugins/pgbouncer.yaml:150,154`; `infra/k3s/base/plugins/networkpolicy-plugin.yaml:121` | не был описан, добавлен задачей 14 | Требует присутствия селектора и порта `6432` (основной путь `TENANT_DB_DSN`). |
| `plugin-gateway` | `app: plugin-gateway` | `infra/k3s/base/plugins/plugin-gateway.yaml:163,167`; `infra/k3s/base/plugins/networkpolicy-plugin.yaml:54,67` | `app: plugin-gateway` (совпадает) | Требует селектор в ingress **и** egress, порт `8080`; НЕ `pod-gateway :8001`. |
| `egress-proxy` | `app: egress-proxy` | `infra/k3s/base/plugins/egress-proxy.yaml:141,145`; `infra/k3s/base/plugins/networkpolicy-plugin.yaml:79` | `app: egress-proxy` (совпадает) | Требует селектор и порт `8080`; запрещает `ipBlock 0.0.0.0/0` и `:80/:443` в plugin-NP (прямой интернет в обход прокси, §6.3). |
| `kube-dns` | `k8s-app: kube-dns` | `infra/k3s/base/plugins/networkpolicy-plugin.yaml:91`; `infra/k3s/base/plugins/networkpolicy-agent.yaml:77`; `infra/k3s/base/agentscale-sandboxes-ks/networkpolicy.yaml:42` | `k8s-app: kube-dns` (совпадает) | Требует селектор, порт `53` и `namespaceSelector=kube-system` (единственное исключение из `agentscale-dev`). |
| `pod-gateway` (только agent) | `app: agentscale-api` | `infra/k3s/base/plugins/networkpolicy-agent.yaml:66`; `infra/k3s/base/agentscale-api/deployment.yaml:36` | `:8001` у агентов | Ожидается ТОЛЬКО в agent-NP; в plugin-NP порт `8001` → `PolicyAlignmentError(reason="plugin_port_forbidden")`, критерий №17. |
| `prodavan` (платформенный PG) | `app: agentscale-postgres` | `infra/k3s/base/plugins/networkpolicy-plugin.yaml:13,102,152` (упоминается как ЗАПРЕЩЁННЫЙ) | отсутствует | Включён в `FORBIDDEN_SELECTORS`: любое появление → ЗАПРЕТ применения (§2.1, §6.2). |

Дополнительно барьер проверяет: `kind/apiVersion`, `metadata.name`
(`plugin-egress` / `agent-egress-reference`), `spec.podSelector`
(`app: plugin` / `app: agent` — иначе NP накроет чужие поды),
`policyTypes == [Ingress, Egress]` (отсутствие `Egress` = «разрешить весь
исходящий трафик»), полноту набора обязательных селекторов (пропущенное
правило = тихая дыра), и что **в agent-NP нет портов 5432/6432/9000/9092/8080**
(§6.1, §13.3).

### Почему «не просто проверить», а запретить

Функция возвращает `None` только при доказанном совпадении и **ничего не
применяет**. Единственная санкционированная точка применения —
`apply_generated_policy_guarded(np_dict, scope, apply, ...)`: сначала assert,
и только потом вызов `apply`. При расхождении `apply` физически не вызывается —
политика не доходит до Kubernetes. Ошибка `PolicyAlignmentError` всегда несёт
`selector` (конкретный селектор), `scope` и машинный `reason`, и наследует
`EgressPolicyError` → `EgressDenied` → `AppError`, то есть отдаётся как
problem+json без дополнительного маппинга.

Для CI/тестов есть `manifest="auto"`: дополнительно читается живой манифест
задачи 14 (`networkpolicy-plugin.yaml` / `networkpolicy-agent.yaml`) и сверяется
с `AS_BUILT_SELECTORS`, чтобы поймать **дрейф в обратную сторону** — если лейбл
переименовали в k3s, а замок не обновили. Если файл недоступен → отказ, а не
«проверку пропустили». В рантайме API `infra/**` нет, поэтому штатный режим
`manifest=None` работает по замороженному снимку.

---

## 2. Почему egress из `CapabilityGrant`, а не из JWT

| | Из JWT (`PluginClaims`) | Из `CapabilityGrant` в БД |
|---|---|---|
| Срок жизни | короткий TTL 300 с, новый при wake-up (§4.2) | долгоживущий, до `revoke` |
| Что происходит при истечении | Envoy-кластеры и NP пересобираются **посреди активного запроса** | правила не меняются; токен несёт только `grant_id` |
| Отзыв доступа | `bump_pod_bridge_generation`, привязан к `pod_id` + Redis (агентская модель) | деактивация grant + проверка `grant_id` на каждом запросе (критерий №12) |
| Доверие к payload | злоумышленник может подсунуть `egress`/`allowed_hosts` в claims | поля читаются платформой из своей БД |

Дизайн v4 §4.2 **убрал** `egress.allowed_hosts` и `quotas` из токена; §6.1 и §6.3
фиксируют источником `CapabilityGrant`; §13.3 прямо называет «egress из JWT»
отвергнутым решением; критерий №22 требует, чтобы истёкший токен не менял
allowlist. Как это уже реализовано задачей 08:
`egress/resolver.py:52-71` (`_claim_grant_id` читает из claims ТОЛЬКО `grant_id`),
`:106-150` (`GrantPolicyResolver.resolve` → БД → `resolve_policy`),
`egress/policy.py:260-342` (`resolve_policy(grant)`),
`egress/generator.py:102-198` (`build_envoy_routes` ставит
`_meta.source = "CapabilityGrant"`, `grant_id` из grant).

`assert_policy_source_is_grant(...)` — второй барьер `alignment.py`. Он проверяет
**всю цепочку**, а не только итог:

* `source` у `EgressPolicy` / Envoy-конфига обязан быть `CapabilityGrant`
  (`"JWT"`, `"PluginClaims"`, `"token"` → отказ, `reason="source_is_jwt"`);
* `grant_id` обязан присутствовать и совпадать с `grant_id` из токена;
* в `claims` / `token` запрещены `egress`, `allowed_hosts`, `allowed_paths`,
  `quotas`, `max_egress_bytes` — `assert` на отсутствие полей JWT;
* в `chain` запрещены маркеры `token.egress`, `claims.allowed_hosts`,
  `jwt.allowed_paths`, `claims.quotas` и им подобные; но обязан быть хотя бы один
  маркер резолвинга по grant (`grant_id`, `GrantStorePort.get_grant`,
  `CapabilityGrant.egress`, `resolve_policy` …);
* цепочка не передана и `source` не доказан → отказ (`chain_missing`). Никакого
  «ну наверное, это из grant».

---

## 3. Почему топики создаёт отдельный Job

| Факт | Где |
|---|---|
| init-Job создаёт **ровно 9** платформенных топиков; `agentscale.plugin.events` и `.dlq` среди них НЕТ | `infra/k3s/base/platform/kafka.yaml:210-218,229-231` |
| `auto_create_topics_enabled=false` — топик не появится «сам» при первом produce | `kafka.yaml:221` |
| `PluginEventDispatcher` обязан вызвать `ensure_topic` до первой отправки; повторная проверка существования после `create_topic` | `apps/api/src/prodavan/application/plugins/events/topics.py:155-251` |
| Канонические имена: `agentscale.plugin.events`, суффикс `.dlq` | `topics.py:35,39`, `dlq_topic()` `:107-120` |
| `RF=1`, `partitions=1` (single-node Redpanda, `replicas: 1`) | `topics.py:41-45`, `kafka.yaml:49` |
| `cleanup.policy=delete`, `log_retention_ms=604800000` (7 суток) | `kafka.yaml:226,233`, `topics.py:38` |

`kafka.yaml` **не редактируется**: это уже применённый Sync-хук с
`hook-delete-policy: BeforeHookCreation,HookSucceeded`, его правка изменила бы
платформенный контракт 9 топиков и создала гонку с порядком sync. Отдельный Job
`infra/k3s/base/platform/plugin-topics-init.yaml`:

* `kind: Job` (group `batch`) — **уже в whitelist**
  `infra/argocd/apps/00-project.yaml:62-63`; новых kinds/CRD нет;
* те же `hook: Sync`, `sync-wave: "5"`, `hook-delete-policy`, `ttlSecondsAfterFinished`,
  `backoffLimit`, образ `ghcr.io/ne-tort/redpanda:v24.2.4` и `ghcr-pull` — по
  образцу `kafka.yaml:155-185`;
* ждёт Kafka API **и** Admin API :9644 с ограничением попыток (`exit 1` при
  таймауте), затем `rpk topic create ... -p 1 -r 1` ровно для **двух** топиков;
* `rpk topic alter-config` ставит `cleanup.policy=delete`,
  `retention.ms=604800000`, `min.insync.replicas=1`;
* **fail-closed:** `set -eu`; `|| true` допущен только на `create` (повторный
  синк) и на `grep -c` (0 совпадений не должен рвать `set -e` раньше явной
  проверки), но после create — обязательный `rpk topic list | grep -qx` для
  каждого топика и проверка, что под BRE-шаблон (см. строку `grep -c` в Job)
  `^agentscale\.plugin\.events\(\.dlq\)\?$` попало ровно **2** имени. Нет топика →
  `exit 1` → Job Failed → Argo Sync хучит, платформа не считается готовой;
* DLQ-имя задано литералом `agentscale.plugin.events.dlq`, а не конкатенацией
  уже-DLQ строки → суффикс `.dlq` **ровно один раз**, цикл `.dlq.dlq`
  исключён (как и в `topics.py:110-120`).

`metadata.namespace` намеренно не задан — подставляет NamespaceTransformer
оверлея (паттерн `kafka.yaml`, `egress-proxy.yaml`).

---

## 4. Чек-лист для родителя (интеграция, выходит за зону этой задачи)

1. ✅ **Подключить Job в kustomization** — **СДЕЛАНО РОДИТЕЛЕМ**: строка
   `- plugin-topics-init.yaml` добавлена в `resources:` файла
   `infra/k3s/base/platform/kustomization.yaml:19` (рядом с `- kafka.yaml`,
   строка 8). Сверено по факту; этот файл в зоне родителя, мной не редактировался.
   Без этой строки манифест остался бы файлом на диске и не попал бы в кластер.
2. Подключить `alignment.py` в `__all__` / публичный контракт `egress/__init__.py`
   — **только после завершения задачи 08**, чтобы не трогать чужой файл в работе.
   Сейчас модуль импортируется напрямую
   (`from prodavan.application.plugins.egress.alignment import ...`) и в
   реэкспорте не нуждается.
3. Встроить `apply_generated_policy_guarded` в точку применения NP (задача 08 /
   controller, обновляющий ConfigMap `egress-proxy-config` и рендерящий NP):
   **ни одного `kubectl apply` / `apply(np)` мимо барьера**. Рекомендуемый вызов
   в CI/Verify Dev — с `manifest="auto"`, в рантайме — `manifest=None`.
4. Вызвать `assert_policy_source_is_grant` перед записью сгенерированного
   `envoy.yaml` в ConfigMap и перед отдачей egress-решения, передав
   `chain` из `resolver.py` (`grant_id → GrantStorePort.get_grant →
   CapabilityGrant → resolve_policy`).
5. Сверить с задачей 15: `/data` ConfigMap `egress-proxy-config` уже должен быть
   в `ignoreDifferences` Argo Application `plugin-platform` (см.
   `egress-proxy.yaml:45-49`), иначе `selfHeal` сотрёт выданные разрешения.
6. Acceptance-карта: к критерию №20 добавить тест «Job создаёт ровно 2 топика с
   RF=1»; к №22 — тест «истёкший `PluginClaims` не меняет allowlist из grant»;
   добавить критерий «NP с `app: minio` → `PolicyAlignmentError`, apply не вызван».
7. При переносе в prod заменить `PLATFORM_NAMESPACE = "agentscale-dev"` на
   `agentscale` (параметр `platform_namespace=`) и имя в `namespaceSelector`
   манифестов задачи 14 — **только по явному требованию** (политика сред).
8. Проверить, что `networkpolicy-plugin.yaml:45-57` ingress-правило использует
   `from`, тогда как `_peer_rule` в `generator.py:454-472` всегда отдаёт `to`.
   Сверка селекторов в `alignment.py` принимает оба ключа, но **структурное**
   расхождение gen → manifest остаётся открытым вопросом для задачи 08 (не правлю
   чужой файл). См. риски ниже.

---

## 5. Риски и открытые вопросы (родителю)

| Риск | Комментарий / mitigations |
|---|---|
| **Структурное расхождение ingress `from`/`to`** | `generator.py:350-356` строит ingress-правило через `_peer_rule`, который кладёт `to`, а эталон задачи 14 `networkpolicy-plugin.yaml:48-57` использует `from`. Для Kubernetes ingress-правило с ключом `to` — как минимум подозрительное поле. `alignment.py` сравнивает СЕЛЕКТОРЫ по обоим ключам и потому не ловит эту разницу как ошибку (иначе замок блокировал бы уже работающий генератор). Нужна правка в зоне задачи 08: отдельный метод для ingress с `from`. **Зафиксировано, не исправлено (чужой файл).** |
| Дрейф as-built лейблов | Ловится только при `manifest="auto"` (CI/Verify Dev). В рантайме сверка идёт по замороженному `AS_BUILT_SELECTORS`. Если манифест k3s изменят без обновления замка — рантайм продолжает требовать старый лейбл (отказ, а не тихая дыра). Это приемлемо: fail-closed. |
| `AS_BUILT_SELECTORS` дублирует константы `generator.py:41-48` | Сделано намеренно: независимый источник истины, который ловит регрессию генератора. Синхронизировать при изменении лейблов вместе с `AS_BUILT_PROVENANCE`. |
| Один `EgressScope` из `StrEnum` | `_normalize_scope` принимает и строку, и enum через `.value`, не импортируя генератор — модуль не зависит от файла задачи 08, который ещё в работе. |
| Проверка `rpk topic list \| grep -qx` | Требует `grep` в образе Redpanda (есть, как и в `kafka.yaml:121` используется `/bin/sh`). При смене образа на минимальный — перепроверить. Альтернатива — `rpk topic describe "$t"`; оставлен `list` как в эталоне. |
| Два Sync-хука одной wave 5 | Разные `metadata.name`, разные Pod'ы, `hook-delete-policy` у каждого свой; оба читают/пишут один брокер. `alter-config` в новом Job трогает ТОЛЬКО 2 своих топика, cluster config не меняет → конфликта с `agentscale-kafka-init` нет. |
| `retention.ms=604800000` | Согласовано с `kafka.yaml:226`. Если DLQ нужен иной retention — править только новый Job. |
| RF=1 | Известный риск single-node (topics.py:41-43), не исправляется в этой задаче. |

---

## 6. Тесты (написаны, НЕ запускались)

`apps/api/tests/unit/plugins/egress_alignment/**`:

* `test_alignment.py` — NP с `app: minio` → `PolicyAlignmentError` + `apply` НЕ
  вызван; NP с as-built лейблами → OK (в т.ч. `manifest="auto"` по живому
  манифесту); agent-NP не содержит 5432/9000/9092; запрет `app: kafka`,
  `app: agentscale-postgres`, `ipBlock`, `policyTypes` без `Egress`,
  пропущенный селектор, неверный `namespaceSelector`, имя NP, subject-селектор.
* `test_source.py` — egress из `token.egress` → отказ; из `claims.allowed_hosts`
  → отказ; из `CapabilityGrant` по `grant_id` → OK; пустая цепочка / битый
  `source` / отсутствие `grant_id` → отказ.
* `test_plugin_topics_init.py` — разбор YAML Job: ровно 2 `rpk topic create` с
  `-p 1 -r 1`, DLQ-суффикс `.dlq` один раз (не `.dlq.dlq`), `cleanup.policy`,
  `retention.ms`, повторная проверка существования и `exit 1`, Sync-хук wave 5,
  `kind: Job` в whitelist `00-project.yaml`.

**Тесты, сборки, линт, компиляция, `git`, `kubectl`, `kustomize` не запускались**
(verification freeze, `plugin-migration-plan.md` §0.2).
