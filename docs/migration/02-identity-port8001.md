# 02 — Identity и порт 8001 (критический модуль)

## Требование

Под агента должен ходить в платформенный API (`:8001`) с идентификацией, привязанной к поду/проекту/юзеру. Токен не обязан быть JWT, но обязан: (а) верифицироваться API, (б) ревокаться при pause/terminate/reload, (в) не попадать в Pod-спеку/env при создании (warm pool + env=`Disallowed`).

## As-built (что имеем)

- Bridge JWT HS256: `typ=pod_bridge, aud=prodavan-pod-bridge, sub=pod:{pod_id}`, claims `{project_id, cabinet_id, company_id, pod_id, gen, scopes[], jti}`, TTL 86400.
- Ревокация: Redis `pod_bridge:gen:{pod_id}`, bump при pause/terminate/reload/force_kill; verify сравнивает gen. Fail-closed (strict) → 503 при лежащем Redis.
- Скоупы: платформенные (`agent:events, internal:credentials, internal:hydrate, infra:*`) + модульные `module:{id}:rows|actions|meta`.
- **Доставка — дефектная**: токен запекается в Pod-спеку как env `PRODAVAN_AUTH_TOKEN` (утечка через `kubectl describe`, `/proc/1/environ`; аудит AI-P1e).
- `:8001` — `main_pod.py` (роутеры health/agent/tenant_infra/pod_modules/internal/pods), NetworkPolicy пропускает только ns `prodavan-sandboxes` → TCP 8001.

## Решение (миграция)

**Контракт Bridge JWT сохраняется целиком** (mint/verify/gen — код `pod_identity/bridge.py` не меняется). Меняется только доставка:

### Доставка post-Ready («token push»)

1. API вызывает `create_sandbox` → watch `Ready`.
2. После `Ready=True` API выполняет `register_session`-подобный **bootstrap-вызов к agent-runtime**:
   `POST http://<router>/…` + `X-Sandbox-ID` + `Authorization: Bearer <pod_agent_runtime_token>` (платформенный токен API→runtime, из Secret `pod-agent-bridge-auth`, уже существует):
   ```json
   POST /v1/identity
   {"bridge_token": "<Bridge JWT>", "pod_id": "...", "project_id": "...", "expires_at": "..."}
   ```
3. agent-runtime хранит токен **в памяти процесса** (не на PVC!) и использует его как `Authorization: Bearer` для всех вызовов `:8001`. `expires_at` — для локального re-request (см. ниже).
4. При 401 от `:8001` (token revoked: pause→resume сменил gen) agent-runtime **пин`ует API `POST /v1/identity/refresh`** через тот же платформенный токен → API минтит новый Bridge JWT (gen актуален) → возвращает. Пробой — платформенный токен знает только runtime и API, утечка через Pod-спеку исключена.

### Почему так

- env в claim = cold start мимо пула + `envVarsInjectionPolicy: Disallowed` отвергнет claim. Секрет в Pod-спеке (secretKeyRef) — та же теплопроблема: warm-под уже создан до юзера, Secret per-project не смонтирован.
- Secret-volume late-bind (монтирование Secret в живой под) — отдельный DaemonSet/ privileged-механика (паттерн latebind-storage) — избыточно против HTTP-push.
- Execution-scoped токены (containarium) — для внешних провайдерских ключей; у нас токен — к **нашему** API, ревокация через Redis-gen уже работает и остаётся.
- Refresh-пин — устраняет race «token истёк/отозван во время длинного turn».

### Альтернативы (рассмотрены и отвергнуты)

| Вариант | Почему нет |
|---|---|
| JWT в claim env | cold start; Disallowed-политика отвергнет |
| Per-pod Secret + secretKeyRef в шаблоне | Secret должен существовать до создания warm-пода (per-project невозможно: пул общий) |
| Scoped-token v2 sandbox-router для pod→API | роутер-токены авторизуют доступ **к сандбоксу**, не от сандбокса к платформе; ревокация — по exp, не по событию pause |
| mTLS client-cert per pod | сертификат тоже надо доставить в живой под — та же проблема, сложнее |
| Downward API + проверка pod-identity в API (Projected ServiceAccount Token) | под уже имеет SA `prodavan-project-pod`; API мог бы верифицировать SA-token через TokenReview и мапить pod→project по лейблу. **Честная альтернатива**, но: SA-token ротируется kubelet'ом автоматически (хорошо), однако скоупы/модули и gen-ревокация требуют всё равно внешнего токена; оставляем как fallback-вариант если push-канал не взлетит |

### Projected SA token как fallback (запасной план, تصمim при реализации)

Если HTTP-push окажется неприемлемым: agent-runtime использует projected SA token (`audience: prodavan-pod-api`), API верифицирует через TokenReview, мапинг pod→project через label `prodavan.io/project-id` на Sandbox (доверие = NetworkPolicy + admission). Ревокация pause — через NetworkPolicy (egress к 8001 уже закрыт при suspend — пода нет). Bridge JWT тогда остаётся только для скоупов модулей.

## :8001 surface — без изменений

- `main_pod.py` остаётся: роутеры health/agent/tenant_infra/pod_modules/internal/pods.
- **Удаляется** `GET /internal/pods/{pod_id}/workspace-archive` (hydrate через initContainer умирает — PVC). Скоуп `internal:hydrate` и код `workspace_tar_download.py` — под удаление после стабилизации (см. 04, Phase: dehydrate-snapshot).
- `/internal/pods/{pod_id}/credentials` (AI key leases) остаётся — AI-ключи продолжают уходить по lease-механизму через Bridge JWT.
- NetworkPolicy: правило egress → ns prodavan TCP 8001 **сохраняется** (селекторы обновить, 03).

## Security-модель итога

| Секрет | Где живёт | Кто доставляет | Утечка через Pod-спеку |
|---|---|---|---|
| Bridge JWT (pod→:8001) | память agent-runtime, mint в API | HTTP post-Ready push | **нет** |
| `pod_agent_runtime_token` (API→runtime) | Secret `pod-agent-bridge-auth` (env API-деплоя) | env Deployment prodavan-api (только API) | нет |
| `POD_IDENTITY_BRIDGE_SECRET` (HS256) | Secret prodavan-api-secrets | только API | нет |
| AI-ключи провайдеров | PG + lease через :8001 | runtime тянет lease по Bridge JWT | нет |

Открытые вопросы реализации (в 07 Phase 2): TTL push-токена против TTL сессии агента; пин refresh при suspend>24ч (TTL 86400) — agent-runtime должен уметь refresh при пробуждении.
