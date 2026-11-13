# Static-origin evidence (B1) — sanitized config plan, NO live evidence

Дата: 2026-11-12. Scope: **B1 evidence-only** — CI static-origin CSP/digest
probe для release evidence gate. Это **не** public browser static hosting,
**не** production/live acceptance, **не** разрешение на rollout и **не** снятие
STOP-SHIP.

Обновление 2026-11-12 (R1 + as-built):

1. Dev HTTP allowlist для static-origin **реализован offline** в
   `.github/scripts/plugin_publication.py` + 8 regressions
   (`StaticOriginDevHttpAllowlistTests`).
2. **Реализован cluster-internal контур:** ASGI
   `apps/api/src/prodavan/main_plugin_static.py` +
   `PluginStaticEvidenceService`
   (`application/plugins/static_origin/service.py`), unit tests
   `apps/api/tests/unit/plugins/static_origin/`, манифесты
   `infra/k3s/base/plugins/plugin-static.yaml`, dev-only NetworkPolicy patch
   (CI runner VM `172.31.156.203/32` → TCP 8081), SealedSecret `.example`,
   регрессии `infra/ops/tests/test_plugin_static_manifest.py`.
3. **Verification (offline):** full backend plugin suite **4251 passed / 1
   skipped**, static_origin **85 passed** (включая 4 builder-entry bridge
   регрессии `test_builder_entry_binding.py`), infra/ops **156 passed**, scoped
   ruff passed; focused stdlib (эта сессия, ранее):
   `test_ci_plugin_publication.py` **71/71**,
   `test_ci_plugin_release_evidence.py` **25/25**, Python 3.13.3.

**UNVERIFIED live:** Service не задеплоен, SealedSecret/ConfigMap/protected
config не заполнены, evidence probe не выполнялся. B1 = offline implemented
+ live blocked by deployment/config. НЕ public browser static hosting.

Связи: открытый blocker B1 — [FINAL-REPORT.md](FINAL-REPORT.md) (§1.3 OPEN,
§7.6); authoring-документация и target route contract —
[docs/PLUGIN-AUTHORING.md](../../docs/PLUGIN-AUTHORING.md), §9 «Static-origin
evidence — cluster-internal `plugin-static`».

## 1. Фактическое состояние на 2026-11-12 (проверено по worktree, offline)

- **В live ничего не развёрнуто и не настроено.** Реализация (route code,
  Service/Deployment/NetworkPolicy manifests, SealedSecret `.example`,
  tests) существует только в worktree; в кластер не применялась, namespace
  `agentscale-dev` не изменялся, Ingress не добавлялся, protected config не
  обновлялся.
- **Никаких live действий:** `gh`, `kubectl`, network/probe команды не
  выполнялись; GitHub variables (включая `PLUGIN_PLATFORM_PUBLICATION_CONFIG`
  и `PLUGIN_PLATFORM_DEV_HTTP_ALLOWLIST`) **не изменялись**; `gh variable
  set` не запускался.
- **Никаких секретов в отчёте:** protected config values, OIDC/GitHub App
  tokens, GHCR credentials не читались, не копировались и не печатаются.
  Документ содержит только публичные контракты кода и placeholder-формы.
- **Успешный CI run / publication / promote не заявляются.** B1 остаётся OPEN;
  этот файл — план и evidence-контракт, а не результат.

As-implemented опоры плана (прочитаны в worktree, без выполнения):

| Контракт | Файл |
|---|---|
| `StaticResponsePolicy`, `response_headers()`, canonical digest | `apps/api/src/prodavan/application/plugins/gateway/ports.py` |
| `HttpxOciArtifactLoader` (digest-verified manifest/blob, caps, no redirects, `trust_env=False`) | `apps/api/src/prodavan/application/plugins/artifacts/publication.py` |
| `GhcrRegistryAuthorizer` (`registry_read`, org-bound, sanitized errors) | `apps/api/src/prodavan/infrastructure/plugins/artifacts/ghcr_auth.py` |
| OCI layout: 3 слоя, frontend `frontend/dist` = `application/vnd.agentscale.plugin.frontend` | `apps/api/src/prodavan/application/plugins/artifacts/layers.py` |
| `exact_https_origin` / `exact_plugin_origins` | `apps/api/src/prodavan/domain/plugins/origins.py` |
| CSP probe (HEAD), `validate_csp_response`, `_static_security_headers`, `StaticOriginConfig`, dev allowlist | `.github/scripts/plugin_publication.py`, `.github/scripts/plugin_release_evidence.py` |
| Quarantine/release refs (`plugins-quarantine/<id>/bundle`, `plugins/<id>/bundle`) | `.github/workflows/plugin-artifacts.yml` |
| Evidence ASGI + service: `prodavan.main_plugin_static:app`, `PluginStaticEvidenceService` (quarantine-first, release только после proven 404; полный OCI contract + canonical config JSON + frontend `tar.gz` iterate-only; bounded LRU/singleflight/negative cache; typed 404/503/413; health gates) | `apps/api/src/prodavan/main_plugin_static.py`, `apps/api/src/prodavan/application/plugins/static_origin/service.py`, tests `apps/api/tests/unit/plugins/static_origin/` |
| Cluster-internal manifests: ServiceAccount без токена, ConfigMap `plugin-static-config`, Deployment :8081 (readOnlyRootFilesystem, runAsNonRoot 10001), Service ClusterIP, NetworkPolicy `plugin-static-egress`; dev-only runner patch `172.31.156.203/32` TCP 8081 | `infra/k3s/base/plugins/plugin-static.yaml`, `infra/k3s/overlays/dev/plugins/patch-plugin-static-runner-ingress.yaml`, `infra/k3s/overlays/dev/plugins/kustomization.yaml`, `infra/ops/tests/test_plugin_static_manifest.py` |
| SealedSecret example (НЕ подключён в kustomization): Opaque `plugin-module-source-github-app`, key `private-key` = PEM; `k8s-secret://` REF + read-only mount; missing file = fail-closed | `infra/k3s/overlays/dev/plugins/plugin-module-source-github-app.sealed.yaml.example` |
| Dev HTTP allowlist теперь покрывает static-origin: `StaticOriginConfig.parse(…, dev_allowlist)` → `SourceBinding.parse` → `from_environment`; `_static_origin_url_allowed` (HTTPS без изменений, HTTP — только exact origin membership; template probe **и** formatted `url_for`); wildcard entries `DEV_ALLOWLIST_INVALID` | `.github/scripts/plugin_publication.py`, regressions `StaticOriginDevHttpAllowlistTests` в `apps/api/tests/unit/plugins/test_ci_plugin_publication.py` |

## 2. As-built contract — cluster-internal readiness route (v1)

Отдельный cluster-internal Service `plugin-static` в `agentscale-dev`,
порт `8081`. **Без Ingress**, **без write API** (только `GET`/`HEAD`,
остальные методы — `405`), **без tenant MinIO/DB/RBAC credentials**.

```
GET|HEAD http://plugin-static.agentscale-dev.svc.cluster.local:8081/internal/plugin-static/v1/bundles/{plugin_id}/{version}/{bundle_digest}/ready
```

- `200` возвращается **только после реальной digest-верификации**:
  полный OCI manifest contract (schema/mediaType/artifactType, аннотации
  `plugin_id`/`manifest_version`, config descriptor, ровно один
  `frontend-dist` layer), canonical config JSON bytes, frontend `tar.gz`
  (iterate-only, без `extractall`).
- **Builder↔service entry contract:** CI manifests эмитят
  `agentscale.plugin.frontend.entry`; service принимает legacy manifests без
  annotation — entry подтверждается digest-verified canonical config JSON,
  mismatch отклоняется (bridge: реальный `build_bundle_manifest` → ready).
- **Cold fetch + bounded cache:** `HttpxOciArtifactLoader` digest-verified
  fetches (`ghcr.io` allowlist, no redirects, bounded caps); verified
  results — bounded in-memory LRU + per-digest singleflight + короткий
  negative cache; disk cache/MinIO/DB отсутствуют.
- **Quarantine→release:** release только после **доказанного registry 404**
  в quarantine (`ghcr.io/<org>/plugins-quarantine/<plugin_id>/bundle@digest`
  → `ghcr.io/<org>/plugins/<plugin_id>/bundle@digest`, тот же digest).
- **Failure semantics (typed, sanitized):** `404 BUNDLE_NOT_READY` (invalid
  identity, proven double-404, digest mismatch, contract/incomplete bundle);
  `503 EVIDENCE_UNAVAILABLE` (любой non-not-found сбой — без fallback/
  retries, не negative-кэшируется); `413 BUNDLE_TOO_LARGE` (caps). Non-200
  не несёт policy headers; клиентские `X-AgentScale-*` не evidence; CI
  `validate_csp_response` fail-closed по `status != 200`.
- **Ответы:** `HEAD 200` — пустое тело + `StaticResponsePolicy` headers;
  `GET 200` — minimal sanitized JSON metadata (no blobs/secrets); прочие
  методы `405`.
- **Health:** `/health/ready` = configured-gate (200 только composed
  loader+policy, иначе 503); `/health/live` — cheap; unconfigured под =
  liveness up + readiness/evidence 503 fail-closed. Пробы в GHCR не ходят.
- **GHCR auth:** только `GhcrRegistryAuthorizer` `registry_read`
  (`packages:read`, `metadata:read`), org-bound; ключ — `k8s-secret://` REF
  + SealedSecret mount, без логирования; tenant secrets отсутствуют.

Response `200` — ровно server-owned `StaticResponsePolicy`:

| Header | Значение |
|---|---|
| `X-AgentScale-Bundle-Digest` | `sha256:<bundle_digest>` (после verify) |
| `Content-Security-Policy` | `default-src 'self'; base-uri 'none'; object-src 'none'; frame-ancestors <plugin_shell_origin>` |
| `X-Content-Type-Options` | `nosniff` |
| `Referrer-Policy` | `no-referrer` |
| `Permissions-Policy` | `camera=(), microphone=(), geolocation=()` |

`plugin_shell_origin` — exact canonical HTTPS origin, совпадающий с API
`PLUGIN_SHELL_ORIGIN` (server-derived; dev-значение в tests —
`https://agentscale.dev`). Static ≠ shell (`exact_plugin_origins`).
`security_headers` в protected config — ровно 4 lowercase-ключа, значения
байт-в-байт из `_static_security_headers(shell_origin)`.

## 3. Sanitized config plan — НЕ выполнен

План для авторизованного оператора. **Значения конфига и секреты нигде не
печатаются**; команды приведены только как форма и **не запускались**.

### 3.1. Предусловия (все ещё не выполнены)

1. **CI Images rebuild** `ghcr.io/ne-tort/agentscale-api:latest` с entrypoint
   `prodavan.main_plugin_static` (merge → CI Images; `imagePullPolicy:
   Always`). НЕ выполнено.
2. **Argo CD sync dev** (`plugin-platform` →
   `infra/k3s/overlays/dev/plugins`): base `plugin-static.yaml` + dev NP
   patch (runner VM `172.31.156.203/32` TCP 8081). `kubectl apply` не
   используется. НЕ выполнено.
3. **SealedSecret `plugin-module-source-github-app`** (dev): Opaque,
   `private-key` = PEM GitHub App; kubeseal dev controller cert,
   namespace/name scope; коммитить только `.sealed.yaml` и подключить в
   kustomization; `.example` не подключать; plaintext/base64 PEM, PAT,
   installation token в Git запрещены. НЕ выполнено.
4. **ConfigMap `plugin-static-config` dev patch**:
   `PLUGIN_MODULE_SOURCE_GITHUB_APP_ID`,
   `PLUGIN_MODULE_SOURCE_GITHUB_APP_INSTALLATION_ID`, trusted
   template/workflow/branch-policy values. `PLUGIN_MODULE_SOURCE_ENABLED=false`
   и `PLUGIN_PLATFORM_ENABLED=false` сохраняются; неполная установка =
   fail-closed typed 503 (не anonymous GHCR). НЕ выполнено.
5. Self-hosted runner VM `172.31.156.203` с доступом к Service (через dev NP
   patch) — live reachability **не подтверждена** (R2); GitHub-hosted
   runners до `*.svc.cluster.local` не достучатся.
6. Dev-only allowlist-расширение static-origin probe — **выполнено offline**
   (R1 закрыт в коде, UNVERIFIED live): exact origin membership для
   `http://`, wildcard запрещён, formatted `url_for` повторно проверяется.

### 3.2. Изменение protected config (shape, без значений)

Только поле `static_origin` у target source в
`PLUGIN_PLATFORM_PUBLICATION_CONFIG`; остальные ключи
(`schema_version`, `publication_base_url`, `scanner_image`, source
bindings) **не меняются**:

```
sources[0].static_origin: null → {
  "url_template": "http://plugin-static.agentscale-dev.svc.cluster.local:8081/internal/plugin-static/v1/bundles/{plugin_id}/{version}/{bundle_digest}/ready",
  "plugin_shell_origin": "<exact HTTPS shell origin; значение не печатается>",
  "security_headers": "<ровно 4 ключа; = _static_security_headers(shell_origin); не печатаются>"
}
```

`null` остаётся fail-closed: `plugin_publication.py publish` остановится с
`CSP_EVIDENCE_REQUIRED` до platform POST. `publication_base_url` НЕ
перенаправляется на `plugin-static`: write API там отсутствует, POST
`/internal/plugin-artifacts` остаётся на platform API.

### 3.3. Dev HTTP allowlist

В `PLUGIN_PLATFORM_DEV_HTTP_ALLOWLIST` (JSON-список exact `http://` origins,
≤32, без path/query/credentials/wildcards) добавляется origin
`http://plugin-static.agentscale-dev.svc.cluster.local:8081` — только dev;
prod allowlist пуст, static probe только HTTPS. **Implemented offline:**
allowlist теперь применяется и к static-origin — `http://` template/probe и
formatted `url_for` требуют exact origin membership
(`_static_origin_url_allowed`); userinfo/query/fragment/другие схемы и
wildcard entries отклоняются. `publication_base_url` keep прежнее exact-URL
правило; HTTPS поведение не изменено. Regressions:
`StaticOriginDevHttpAllowlistTests` — exact origin+port accepted, другой
host/port rejected, http без allowlist rejected, https без allowlist accepted,
formatted URL не уходит с allowlisted origin, userinfo/query/схемы denied,
wildcard allowlist denied. Focused offline: 71/71 + 25/25 passed.

### 3.4. Offline-валидация перед применением (без сети и печати значений)

- Локально прогнать обновлённый JSON через существующий parser
  (`PublicationConfig.from_environment` / `StaticOriginConfig.parse`) в
  изолированном окружении с подменённым transport; фиксировать только
  результат `OK` или typed code (`CSP_CONFIG_INVALID`,
  `PROTECTED_CONFIG_INVALID`, `DEV_ALLOWLIST_INVALID`…), не значения.
- Проверить: `bundle_digest` присутствует в `url_template`; static ≠ shell;
  `security_headers` точно совпадают с `_static_security_headers`;
  allowlist entries валидны и без дубликатов.
- Файл с новым значением хранить вне репозитория; в git/JUnit/логи/step summary
  не помещать; `gh variable list` и любые команды, печатающие значения, для
  сверки не использовать (только имена/scope).

### 3.5. Применение (форма команд, НЕ ВЫПОЛНЕНО)

```
# НЕ ВЫПОЛНЕНО. Scope (repository vs environment plugin-release) оператор
# подтверждает по существующей настройке без вывода значений.
gh variable set PLUGIN_PLATFORM_PUBLICATION_CONFIG   --env plugin-release --body-file <локальный-файл-вне-репозитория>
gh variable set PLUGIN_PLATFORM_DEV_HTTP_ALLOWLIST   --env plugin-release --body '["http://plugin-static.agentscale-dev.svc.cluster.local:8081"]'
```

### 3.6. Rollback

Вернуть предыдущее значение переменной из защищённого хранилища оператора.
Откат на `static_origin: null` безопасен по fail-closed: публикация
остановится с `CSP_EVIDENCE_REQUIRED` до любого platform POST; частичной
активации не возникает. Новый parse failure аналогично останавливает path до
registry write.

Инфра-откат (если prerequisites уже применялись): удалить sealed-файл и
ConfigMap/NP patches из kustomization → Argo sync dev возвращает прежнее
состояние; удаление runner patch вновь закрывает доступ VM `172.31.156.203`.
Деструктивных DB-операций нет — контур без БД. Сам откат в этой сессии не
выполнялся (и не требовался): deployment/config не применялись.

## 4. Evidence checklist для будущего авторизованного прогона

Фиксировать только sanitized факты:

- run id, commit SHA, дата, environment=dev, runner type (self-hosted).
- Probe: HTTP status; наличие и точное равенство 4 security headers;
  `X-AgentScale-Bundle-Digest == {bundle_digest}` (boolean).
- Verify: источник `quarantine|release`; manifest digest match (boolean);
  frontend blob digest+size match (boolean); cold fetch выполнен (boolean).
- CI step outcome и typed failure codes при сбое (`CSP_ORIGIN_HTTP_ERROR`,
  `CSP_SECURITY_HEADERS_FAILED`, `CSP_ARTIFACT_BINDING_MISMATCH`…).
- **Никогда:** request/response bodies, tokens, GHCR/OIDC credentials, полный
  protected config, tenant secrets.
- Promotion/release verify/publication фиксируются отдельными evidence; этот
  checklist их не подменяет и не заявляет.

## 5. Риски и открытые пункты

- **R1 — HTTPS-only probe.** ~~Требовалось dev-only allowlist-расширение.~~
  **Закрыто offline:** `_static_origin_url_allowed` принимает `http://`
  только при exact origin в `PLUGIN_PLATFORM_DEV_HTTP_ALLOWLIST`; regressions
  добавлены и выполнены (71/71 + 25/25, Python 3.13.3). **UNVERIFIED live:**
  реальный probe через cluster-internal Service не выполнялся.
- **R2 — Доступность cluster-internal адреса.** Dev-only NetworkPolicy
  patch реализован: ingress peer ровно `172.31.156.203/32` (CI runner VM)
  на TCP 8081, base-правила (plugin-gateway/agentscale-api) сохранены;
  Service ClusterIP, Ingress намеренно отсутствует. **Live DNS/network до
  Service с VM не проверены** — Argo sync и probe не выполнялись.
- **R3 — Нет live evidence.** Реализация и offline verification есть
  (worktree), но Service не задеплоен, SealedSecret/ConfigMap/protected
  config не заполнены, evidence probe не выполнялся. B1 = offline
  implemented + live blocked by deployment/config; STOP-SHIP сохраняется.
- **R4 — Bounded fetch.** Manifest/frontend/tar caps ограничены
  `StaticEvidenceConfig`; превышение = typed `413 BUNDLE_TOO_LARGE`
  (fail-closed, без усечения). Реальные размеры production bundle и
  достаточность caps на live не проверены.
- **R5 — Quarantine lifecycle.** Pre-promotion readiness зависит от наличия
  quarantine артефакта (retention/cleanup); post-promotion — release fallback
  с тем же digest. Partial/transient состояния не считаются absent.
- **R6 — Scope discipline.** План не создаёт public static hosting, не меняет
  `PLUGIN_STATIC_ORIGIN`/bootstrap/`GatewayProxy.forward_static`, не даёт
  сервису tenant MinIO/DB/RBAC и write API. Browser static hosting остаётся
  отдельным unverified scope.
- **R7 — Spoofing.** `200` и headers выдаются только после verify; non-200 не
  несёт policy headers; client-supplied `X-AgentScale-*` игнорируются.

## 6. Unrun / не выполнялось

- Focused offline tests **выполнены** (после R1 реализации):
  `test_ci_plugin_publication.py` **71/71 OK** (включая 8 новых
  `StaticOriginDevHttpAllowlistTests`) и `test_ci_plugin_release_evidence.py`
  **25/25 OK**, Python 3.13.3, stdlib unittest, без сети.
- Verification по сводке 2026-11-12 (после as-built реализации и
  builder→service integration fix): full backend plugin suite **4251 passed /
  1 skipped**, static_origin **85 passed** (+4 builder-entry bridge),
  infra/ops **156 passed**, scoped ruff passed. Это offline/static evidence,
  не live acceptance.
- **Не выполнялось:** deployment (CI Images rebuild, Argo sync dev),
  SealedSecret/ConfigMap заполнение, end-to-end evidence probe. Build/lint
  полного дерева не запускались в этой сессии.
- Network, cluster, deploy, prod, `gh variable set`, чтение protected values —
  не выполнялись. Live success, CI success, promotion и publication не
  заявляются.
