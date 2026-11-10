# Plugin lifecycle — real-K8s acceptance harness (opt-in, disposable cluster)

Назначение: приёмочные проверки **production**-адаптеров
ServiceAccountTokenProvider + K8sApiClient + K8sDeploymentAdmin +
K8sSecretProjection против **настоящего Kubernetes API** в выделенном
одноразовом non-prod кластере. Продакшен-код не меняется; вместо durable
PostgreSQL-репозиториев используются in-memory test doubles только для
artifact/instance registry, tenant SecretPort и webhook resolver — Kubernetes
никогда не подменяется.

## Статус: harness НЕ ЗАПУСКАЛСЯ

Этот пакет написан в рамках migration verification freeze. **Никакие проверки
не выполнялись**: тесты, сборка, lint, compile, docker, kubectl и git-checks не
запускались; live/dev/prod/shared кластеры не трогались. Файлы проверены только
чтением против production-контрактов (token_provider.py, k8s_scale.py,
deployment_admin.py, secret_projection.py, infra/k3s/.../plugin-*-rbac.yaml).

## Что в scope

| Требование | Тесты |
|---|---|
| Явный disposable kubeconfig + namespace prefix, unique suffix, TTL, collected>0/skipped=0 | conftest.py, acceptance_config.py, cluster.py (все тесты opt-in) |
| TokenRequest: API/Gateway mint только для plugin-deployment-admin; unrelated SA deny; audience/TTL≤3600; refresh; failure; shutdown; data path без mounted token | test_token_request_acceptance.py |
| DeploymentAdmin: ensure, downwardAPI plugin_instance_id/backend_digest, owner-token update, same-owner retry, foreign owner/digest/ownership deny до mutation, rollback (digest+owner+удаление annotation)+post-rollback runtime_binding, два lifecycle-процесса, сохранение non-managed sibling volumes/mounts при update/retry/deny/rollback, scoped uninstall + sibling preservation | test_deployment_admin_acceptance.py |
| SecretProjection: ровно 4 managed keys + отдельный delivery Secret, hashed keys/value versions, CAS (recorder rv + реальный stale-409) для lifecycle и delivery, rotation, lifecycle/delivery drift deny/repair, stale key removal, readOnly 0400 mount, revoke/uninstall scoped cleanup, sibling preservation, отсутствие DSN/secret values/токенов в логах | test_secret_projection_acceptance.py (mount — также в test_deployment_admin_acceptance.py) |

## Что НЕ в scope (и не имитируется)

* **SqlPluginUpdateLock требует PostgreSQL и НЕ входит в этот K8s-only harness.**
  Его успех здесь нигде не симулируется; test_scope_is_k8s_only_and_has_no_sql_update_lock
  — структурный страж против подключения SQL-lock/БД-настроек в эту композицию.
  Для lock нужен отдельный PostgreSQL acceptance harness.
* Plugin platform/prod контур не включается: нет GitOps, Argo, overlays,
  plugin-gateway deployment, registry/cosign, sandbox pod'ов.
* Backend Deployment остаётся replicas=0: артефакты синтетические, образов в
  disposable кластере нет. Проверяются реальные объекты API, ownership,
  annotations, downwardAPI и Secret-проекции, а не запущенные контейнеры.
* Выполнение production verification (tests/build/lint/compile) отложено.

## Требования к окружению

1. **Одноразовый non-prod кластер**, созданный только для приёмки (отдельный
   kind/k3d/k3s на выделенной VM). НЕ dev/prod/shared VM 172.31.156.203.
2. Отдельный kubeconfig этого кластера **вне репозитория** (например
   %TEMP%\agentscale-plugin-acceptance.kubeconfig на Windows или
   /tmp/agentscale-plugin-acceptance.kubeconfig). Default ~/.kube/config
   запрещён и отклоняется. Bearer token или client cert/key (data/файлы);
   exec/auth-provider плагины не поддерживаются намеренно.
3. Python-окружение apps/api с установленными зависимостями (pip install -e .[dev]
   или PYTHONPATH=src) — импортируются только production-модули prodavan.*.
4. Сеть до API-сервера. **kubectl не нужен**: весь доступ — через production
   K8sApiClient (httpx REST). SDK/kubernetes-client не используется.

## Переменные окружения (точные имена)

| Env | Обязательна | Default | Ограничения |
|---|---|---|---|
| **PLUGIN_ACCEPTANCE_KUBECONFIG** | да (иначе весь harness skip) | — | путь к disposable kubeconfig вне репо; ~/.kube/config запрещён |
| **PLUGIN_ACCEPTANCE_KUBE_CONTEXT** | нет | current-context | контекст из того же файла; shared/dev/prod-имена отклоняются |
| **PLUGIN_ACCEPTANCE_NAMESPACE_PREFIX** | да | — | DNS-1123 label 1..24; не prod/agentscale/shared/sandbox, не reserved ns; пример: plugin-acc |
| **PLUGIN_ACCEPTANCE_RUN_ID** | нет | случайный 12-hex | DNS-1123 фрагмент ≤20; уникальный suffix прогона |
| **PLUGIN_ACCEPTANCE_TTL_SECONDS** | нет | 1800 | 60..86400; TTL annotation namespace |
| **PLUGIN_ACCEPTANCE_KEEP_NAMESPACE** | нет | 0 | 1 = не удалять namespace после модуля (debug), TTL/janitor продолжают действовать |
| **PLUGIN_ACCEPTANCE_TTL_JANITOR** | нет | 1 | 0 = не удалять протухшие leftovers (namespace-scoped bootstrap) |
| **PLUGIN_ACCEPTANCE_GATEWAY_URL** | нет | http://plugin-gateway.<ns>.svc:8080 | canonical internal Service URL (валидация production RuntimeConfig) |
| **PLUGIN_ACCEPTANCE_TOKEN_TTL_SECONDS** | нет | 3600 | 60..3600; TTL TokenRequest lifecycle identity (≤3600 жёстко) |
| **PLUGIN_ACCEPTANCE_API_TIMEOUT_SECONDS** | нет | 15 | 1..120; таймаут REST-запросов |

Итоговое имя namespace: **<prefix>-<run_id>-<module_tag>**, где module_tag =
token/deploy/secret. Каждый тестовый модуль получает свой namespace.

## Guardrails против чужих контуров

* Нет default kubeconfig, in-cluster SA, KUBECONFIG-env или dev/prod fallback.
* Запрещённые namespace: default, kube-system, kube-public, kube-node-lease,
  agentscale, agentscale-dev, agentscale-prod, agentscale(-dev)-sandboxes.
* Единое fail-closed правило looks_like_shared_contour для context/cluster/host
  имен и namespace prefix: default/dev/prod/production/shared/live/agentscale
  запрещены как exact match и как whole token (разделители . - _ / :), а
  подстроки prod/shared/agentscale — в любом месте идентификатора; prefix
  дополнительно не должен содержать sandbox. Отклоняются: default, dev,
  prod.example, dev.example, shared.example, kubernetes.default.svc, kind-dev,
  api.live.example, my-prod-cluster, prefix dev. Разрешены (не ломаются о
  правило) legitimate random acceptance names: kind-kind, k3d-acceptance,
  acceptance-x7q2, plugin-acc, 127.0.0.1, kind.local.
* Reserved context namespace из списка выше и server host 172.31.156.203
  отклоняются всегда; default ~/.kube/config запрещён.
* Удаляются только namespace этого прогона после повторной проверки
  labels/annotations владения; чужой namespace никогда не усваивается
  (409 на create = ошибка, а не adopt).
* Janitor удаляет только namespace с labels этого harness'а, тем же prefix и
  истёкшей TTL annotation. Ничего вне этого набора не удаляется.

## RBAC

### 1) Bootstrap identity (kubeconfig) — ТОЛЬКО на disposable кластере

Нужны права на создание/удаление одноразовых namespace и объектов внутри них.
Минимальный набор (применять только на кластере, созданном для приёмки):

~~~yaml
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRole
metadata:
  name: agentscale-plugin-acceptance-bootstrap
rules:
  - apiGroups: [""]
    resources: ["namespaces"]
    verbs: ["get", "list", "create", "delete"]
  - apiGroups: [""]
    resources: ["serviceaccounts"]
    verbs: ["get", "create"]
  - apiGroups: [""]
    resources: ["serviceaccounts/token"]
    verbs: ["create"]
  - apiGroups: ["rbac.authorization.k8s.io"]
    resources: ["roles", "rolebindings"]
    verbs: ["get", "create"]
  - apiGroups: [""]
    resources: ["secrets", "services"]
    verbs: ["get", "list", "create", "patch", "delete"]
  - apiGroups: ["apps"]
    resources: ["deployments"]
    verbs: ["get", "list", "create", "patch", "delete"]
---
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRoleBinding
metadata:
  name: agentscale-plugin-acceptance-bootstrap
roleRef:
  apiGroup: rbac.authorization.k8s.io
  kind: ClusterRole
  name: agentscale-plugin-acceptance-bootstrap
subjects:
  - kind: User
    name: agentscale-plugin-acceptance   # заменить на субъект disposable kubeconfig
    apiGroup: rbac.authorization.k8s.io
~~~

Примечания:
* serviceaccounts/token create нужен bootstrap-identity, чтобы выдать
  control-plane (agentscale-api/plugin-gateway/acceptance-unrelated) токены
  для проверок RBAC; lifecycle token затем выпускается production
  TokenProvider'ом через эти control-plane identities.
* При bootstrap, ограниченном заранее созданным namespace, создание namespace
  невозможно: используйте вариант кластера с ClusterRole выше, либо отключите
  janitor (PLUGIN_ACCEPTANCE_TTL_JANITOR=0) и выдавайте namespace-create отдельно.
  Admin kubeconfig shared-кластера использовать ЗАПРЕЩЕНО.

### 2) RBAC внутри каждого disposable namespace (создаёт harness)

Полностью повторяет production-манифесты, но в одноразовом namespace:

* ServiceAccounts: plugin-deployment-admin (automountServiceAccountToken=false),
  agentscale-api, plugin-gateway, acceptance-unrelated (без прав).
* Role plugin-deployment-admin: deployments get/create/patch/delete,
  deployments/scale get/patch, services get/create/patch/delete,
  services/proxy get/create, secrets get/create/patch/delete,
  configmaps get/patch (resourceNames plugin-activation-state) + create,
  pods list. RoleBinding только на plugin-deployment-admin.
* Role plugin-deployment-admin-token-request: serviceaccounts/token create
  с resourceNames=[plugin-deployment-admin]; RoleBinding только на
  agentscale-api и plugin-gateway.

Тесты проверяют это реальными 403: control-plane не может mint для другой SA и
не имеет data-plane прав; unrelated SA не может mint для admin и не читает
Secrets; сам admin не имеет TokenRequest-прав.

## Запуск (когда verification freeze будет снят)

Из apps/api:

~~~bash
export PLUGIN_ACCEPTANCE_KUBECONFIG=/tmp/agentscale-plugin-acceptance.kubeconfig
export PLUGIN_ACCEPTANCE_NAMESPACE_PREFIX=plugin-acc
python -m pytest -c tests/e2e/k8s/plugin_acceptance/pytest.ini --confcutdir=tests/e2e/k8s/plugin_acceptance tests/e2e/k8s/plugin_acceptance -v
~~~

Windows PowerShell (Git Bash не нужен для файловых операций):

~~~powershell
$env:PLUGIN_ACCEPTANCE_KUBECONFIG = "$env:TEMP\agentscale-plugin-acceptance.kubeconfig"
$env:PLUGIN_ACCEPTANCE_NAMESPACE_PREFIX = "plugin-acc"
python -m pytest -c tests/e2e/k8s/plugin_acceptance/pytest.ini --confcutdir=tests/e2e/k8s/plugin_acceptance tests/e2e/k8s/plugin_acceptance -v
~~~

Явный путь tests/e2e/k8s/plugin_acceptance в конце команды ОБЯЗАТЕЛЕН: опция -c
сама по себе не ограничивает коллекцию, а testpaths в pytest.ini используется
только когда аргумент-путь не передан (и парсится shlex, поэтому там именно
testpaths = ., а не ["."], которое стало бы literal bracketed path). Без явного
пути коллекция ушла бы по всему apps/api и потянула parent probe conftests.

Изоляция от umbrella suite (обязательный --confcutdir):

* Промежуточные conftest: tests/e2e/conftest.py существует (импортирует
  tests/conftest.py), а tests/e2e/k8s/conftest.py НЕ существует. Поэтому точный
  confcutdir=tests/e2e/k8s/plugin_acceptance — минимальный, сохраняющий наш
  conftest.py: pytest загружает conftest только на уровне confcutdir и ниже,
  значит tests/conftest.py и tests/e2e/conftest.py не импортируются вообще.
* Это важно, потому что tests/conftest.py при ИМПОРТЕ выполняет side effects:
  live-API HTTP probe (_live_api_available), kubectl auth can-i / in-cluster SA
  probe (_k8s_available) и настройку Postgres wipe/seed фикстур. Acceptance не
  должен ни импортировать их, ни инициировать эти probes.
* --noconftest использовать ЗАПРЕЩЕНО: он отключил бы и наш conftest.py, то есть
  opt-in gating, namespace fixtures, TTL janitor, изоляционные overrides и
  контракт collected>0/skipped=0.
* Статические guards (оба fail-fast с текстом точной команды):
  - pytest_configure при заданном PLUGIN_ACCEPTANCE_KUBECONFIG поднимает
    pytest.UsageError, если parent probe conftest (tests/conftest.py или
    tests/e2e/conftest.py) уже загружен — до любых probes/запросов;
  - pytest_collection_finish поднимает pytest.UsageError, если собран хоть один
    тест ВНЕ plugin_acceptance — collection-scope guard, который не даёт -c без
    явного пути молча собрать весь apps/api. Таким образом коллекция всегда
    ограничена нашим пакетом, а parent probes не загружаются.
* Дополнительно локальный conftest.py переопределяет umbrella autouse-фикстуры
  clean_engine_cache и _auth_command_bus_isolation, поэтому даже при сборке всего
  репозитория этот пакет не вызывает wipe/seed PostgreSQL и не меняет глобальные
  auth/Redis настройки. Sandbox pod cleanup (register_e2e_project) не используется.

Контракт выполнения:
* Без PLUGIN_ACCEPTANCE_KUBECONFIG все тесты skip с явной причиной (по умолчанию
  harness выключен).
* С заданным kubeconfig обязательно collected>0 и skipped=0: локальный
  pytest_sessionfinish выставляет exit code 1 при нарушении, чтобы «приёмка»
  не была молча зелёной.

## TTL и cleanup

* Namespace получает labels agentscale.ne-tort/acceptance=plugin-k8s,
  agentscale.ne-tort/acceptance-prefix, app.kubernetes.io/managed-by=
  agentscale-plugin-acceptance и annotations acceptance-expires-at/run-id.
* Teardown модуля удаляет только свой namespace (повторная проверка владения,
  ожидание исчезновения до 180с). Rollback при падении setup удаляет namespace
  ТОЛЬКО если POST на его создание уже успешно завершился (created_by_me), и
  перед DELETE повторно читает объект и требует совпадения acceptance
  labels/annotations этого прогона: 409/любой сбой до создания или чужие
  markers = НИКАКОГО удаления (чужой namespace никогда не откатывается).
* Janitor при старте сессии удаляет протухшие leftovers того же prefix.
* PLUGIN_ACCEPTANCE_KEEP_NAMESPACE=1 оставляет namespace для разбора; TTL
  annotation остаётся, поэтому следующий прогон janitor его заберёт.

## Секреты и логи

* В репозитории нет plaintext-секретов и admin kubeconfig. Все DSN/webhook/
  capability значения — синтетические sentinel-строки, генерируемые из run_id.
* Токены SA живут только в памяти процесса; repr/redaction у TokenProvider и
  у структур harness (dataclass field repr=False).
* Логи содержат только имена/digest/resourceVersion; SanitizedRecorder не
  сохраняет значения Secret.data (только имена ключей и null-markers).
* test_no_secret_values_or_tokens_in_logs захватывает РЕАЛЬНЫЕ
  plugin-deployment-admin токены обоих lifecycle-провайдеров (процессы A и B) и
  все bootstrap control-plane токены и проверяет raw + base64 формы каждого
  sentinel-значения и токена в captured logs.
* Ни один assert не выводит секреты/values/base64/токены: сравнения значений —
  только по SHA-256 digest (assert_value_digest_matches), полные Secret dict не
  печатаются (assert_objects_equal сообщает лишь label), а leak-guards
  (assert_no_sensitive_material) сообщают только stable code, никогда не
  needle/haystack; сравнения токенов — assert_same_token/assert_token_rotated.
* Отрицательные RBAC запросы выполняются на ОТКРЫТОМ broker до shutdown;
  _assert_forbidden принимает только реальный API-server 403 и явно отклоняет
  closed/client-side ошибки (close semantics проверяются отдельно).

## Файлы

* acceptance_config.py — env-контракт, валидация, guardrails имён.
* kubeconfig.py — разбор disposable kubeconfig, SSL, StaticTokenAuth,
  NoMountedTokenAuth (доказательство «data path без mounted token»).
* cluster.py — namespace/RBAC/TokenRequest bootstrap, TTL janitor, teardown,
  lifecycle transport на production TokenProvider+K8sApiClient.
* lifecycle.py — synthetic ids/artifacts/instances/tenant/webhook doubles,
  два lifecycle-процесса (agentscale-api, plugin-gateway), SanitizedRecorder.
* conftest.py — opt-in gating, namespace fixtures, изоляция от umbrella suite,
  контракт collected>0/skipped=0.
* test_token_request_acceptance.py — 5 тестов TokenRequest identity.
* test_deployment_admin_acceptance.py — 7 сценариев DeploymentAdmin (включая
  сохранение non-managed sibling volumes/mounts при update/retry/deny/rollback
  и scoped uninstall).
* test_secret_projection_acceptance.py — 7 сценариев SecretProjection (включая
  delivery CAS по recorder+stale-409 и delivery drift deny/repair с sibling
  preservation).

Всего 19 acceptance-тестов; без kubeconfig все они skip, с kubeconfig ни один
не должен быть skipped.
