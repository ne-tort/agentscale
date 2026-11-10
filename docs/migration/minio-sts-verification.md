# Проверка MinIO STS для plugin-объектов (v4 §5.4, §12.8)

**Дата:** 2026-11-08 · **Worktree:** `wt-migration` (ветка `main`) · **Статус:** блокер **СНЯТ** выбором fallback.
**Область:** блокер №1 [`docs/migration/plugin-migration-plan.md`](plugin-migration-plan.md) §5 и открытый вопрос №8 [`docs/ARCHITECTURE/plugin-architecture-complete.md`](../ARCHITECTURE/plugin-architecture-complete.md) §12.
**Метод:** только чтение локальных файлов + до 6 попыток `web_fetch` по официальной документации MinIO. Код не правился, тесты/сборки/`git` не запускались. Единственный созданный файл — этот отчёт.

> **Кратко:** официальная документация MinIO получена не была (5 сетевых ошибок + 1 HTTP 403).
> `AssumeRole` / session policy / policy variables / TTL / формат ответа считаются
> **НЕ ПОДТВЕРЖДЁННЫМИ**. По решению родителя v4 по умолчанию использует
> **scoped presigned URL с IAM-политикой на префикс `module-<plugin>/*`**, без выдачи
> плагину каких-либо credentials. `PLUGIN_MINIO_STS_ENABLED=false` — дефолт.
> STS остаётся **опциональной оптимизацией**, включаемой только после ручной проверки
> 8 пунктов на Verify Dev (см. конец документа). Блокер снимается не «ответом ДА/НЕТ»,
> а **зафиксированным выбором безопасного пути по умолчанию**.

---

## Что проверено

### Официальная документация MinIO — `web_fetch`, 6 попыток, 6 URL

| # | URL | Результат |
|---|-----|-----------|
| 1 | https://min.io/docs/minio/linux/developers/security-token-service/AssumeRole.html | `Error: web fetch failed: TypeError: fetch failed` |
| 2 | https://min.io/docs/minio/linux/administration/identity-access-management/policy-based-access-control.html | `Error: web fetch failed: TypeError: fetch failed` |
| 3 | https://raw.githubusercontent.com/minio/minio/master/docs/sts/assume-role.md | `Error: web fetch failed: TypeError: fetch failed` |
| 4 | https://min.io/docs/minio/linux/developers/security-token-service.html | `Error: web fetch failed: TypeError: fetch failed` |
| 5 | https://github.com/minio/minio/blob/master/docs/sts/assume-role.md | `Error: web fetch failed: TypeError: fetch failed` |
| 6 | https://min.io/docs/minio/linux/operations/external-iam.html | `Fetched … (HTTP 403)` — тело: `Request blocked` |

**Итого:** 6/6 попыток не дали содержимого официальной страницы. 5 — отказ сетевого
уровня (`TypeError: fetch failed`), 1 — сервер/прокси отдал **HTTP 403 `Request blocked`**
(доступ до хоста был, но страница заблокирована). Лимит в 6 попыток исчерпан, разные
URL (включая `raw.githubusercontent.com` и `github.com`) использованы.

**Цитат из официальной документации MinIO в этом отчёте НЕТ** — не потому, что их
не нашли, а потому что ни одна страница не была получена. Любая формулировка ниже
про «как работает MinIO» помечена либо как **НЕ ПОДТВЕРЖДЕНО**, либо как
«что заложено в нашем коде», но **не** как факт официальной документации.
Сознательно не подменяем отсутствие источника знанием модели: это ровно тот блокер,
который и требовалось снять честно.

### Локальные источники (прочитаны; **НЕ** являются официальной документацией MinIO)

| Файл | Зачем прочитан |
|---|---|
| `docs/ARCHITECTURE/plugin-architecture-complete.md` v4, §1, §4.3, §5.4, §6.2, §11 №19, §12 п.8, §13.3, §14, финал | формулировка блокера и текущая целевая модель |
| `docs/migration/plugin-migration-plan.md` §3 F3, §5 п.1 | текст блокера «при недоступности — scoped presigned» |
| `apps/api/src/prodavan/application/plugins/objects/policy.py` | генератор `bucket_policy` / `session_policy`, явные литералы вместо policy variables |
| `apps/api/src/prodavan/application/plugins/objects/ports.py` | `StsPort`, `PresignPort`, `ScopedCredentials`, TTL 60..3600 |
| `apps/api/src/prodavan/application/plugins/objects/service.py` | `issue_credentials`, `presign`, `grant`, `revoke`, `_normalize_key`, аудит |
| `apps/api/tests/unit/plugins/objects/*` (просмотрено выборочно через поиск) | что уже покрыто тестами, не запускались |
| `apps/api/src/prodavan/infrastructure/files/minio_store.py:186-207` | as-built presigned `get_object` / `put_object` |
| `infra/k3s/base/platform/minio.yaml` | версия образа и init-Job |
| `infra/k3s/base/platform/minio-api-policy.json` | as-built права service user |

Цитаты из них (для обоснования ниже, **не** как подтверждение поведения MinIO):

- `plugin-architecture-complete.md:903` — «**MinIO STS** — подтвердить официальной
  документацией `AssumeRole` / policy variables (в этом ревью страница была недоступна);
  при отсутствии STS — scoped presigned с policy variables.»
- `plugin-migration-plan.md:146` — «Подтвердить MinIO STS `AssumeRole` / policy variables
  по официальной документации; **при недоступности — scoped presigned**.»
- `plugin-architecture-complete.md:517-518` — «**bucket-per-tenant** `tenant-<company>`
  (v1-компромисс: префикс + MinIO STS `AssumeRole` session policy с `${aws:username}`)» /
  «short-lived STS creds / scoped presigned, ограниченные `module-<id>/*`».
- `plugin-architecture-complete.md:526` — «Presigned уже реализованы в
  `minio_store.py:186-203` для обычного контента; для внешних клиентов сохраняется
  `proxy=1` (in-cluster DNS не резолвится снаружи).»
- `policy.py:12-23` — «конкретный набор переменных, поведение `AssumeRole` с session
  policy и допустимость `StringLike` по `s3:prefix` **ЗАВИСЯТ ОТ ВЕРСИИ MinIO** и требуют
  подтверждения ОФИЦИАЛЬНОЙ документацией MinIO … по умолчанию здесь НЕ используются
  никакие переменные».
- `policy.py:160` — «Передаётся как `Policy=json.dumps(...)` при `AssumeRole`» (намерение
  кода, **не** подтверждённый контракт API).
- `ports.py:25-30` — `MIN_TTL_SECONDS = 60`, `MAX_TTL_SECONDS = 3600`,
  `DEFAULT_TTL_SECONDS = 900` (наше ограничение, **не** границы MinIO).
- `minio.yaml:86` — `image: ghcr.io/ne-tort/minio:RELEASE.2024-10-02T17-50-41Z`.
- `minio.yaml:88-103` — в env только `MINIO_ROOT_USER`, `MINIO_ROOT_PASSWORD`,
  `MINIO_UPDATE=off`, `MINIO_BROWSER=on`; **нет** `MINIO_IDENTITY_OPENID_CONFIG_URL`,
  нет LDAP-настроек, нет STS-специфичных переменных.
- `minio-api-policy.json:6-13` — as-built service user имеет `arn:aws:s3:::agentscale`
  и `arn:aws:s3:::agentscale/*` целиком (то есть **не** ограничен префиксом).
- `apps/api/src/prodavan/infrastructure/plugins/minio/adapters.py` — **файл отсутствует**
  в worktree на момент проверки (задача MinIO IAM ещё не создала адаптер); значит
  проверить поведение по локальной реализации сейчас невозможно.

---

## Ответы на 6 вопросов

Общий статус: **НЕ ПОДТВЕРЖДЕНО** по всем шести. Причина одна и та же — официальный
источник не получен (см. таблицу выше); ниже уточняется, что именно осталось
недоказанным и что уже безопасно заложено в коде.

### 1. `AssumeRole` (STS): поддержка, версия, доступность в `RELEASE.2024-10-02T17-50-41Z`

**НЕ ПОДТВЕРЖДЕНО.** Ни одна из четырёх целевых страниц (`AssumeRole.html`,
`security-token-service.html`, `docs/sts/assume-role.md` в raw и в github) не открылась;
`external-iam.html` вернул 403. Нет ни даты/версии появления STS, ни подтверждения, что
конкретный образ `ghcr.io/ne-tort/minio:RELEASE.2024-10-02T17-50-41Z`
(`minio.yaml:86`) содержит рабочий эндпоинт STS. Дополнительно: это **зеркало** образа
в GHCR, а не прямой `quay.io/minio` — даже совпадение тега не доказывает идентичность
сборки без проверки digest. Код `StsPort.assume_role` (`ports.py:143-161`) и
`session_policy()` (`policy.py:157`) описывает **намерение**, а не подтверждённую
возможность развёрнутого сервера.

### 2. Session policy (inline `Policy` при `AssumeRole`), подмножество родительской, параметр `Policy`

**НЕ ПОДТВЕРЖДЕНО.** Официальное описание параметра `Policy`, требование «session
policy ⊆ родительская» и поведение пересечения прав не получены. В коде это требование
**уже реализовано как внутренняя гарантия**: один генератор `_statements()` на обе
политики (`policy.py:90-96`), проверка `_is_subset()` в `service.py:383-386,697-712`,
`Policy=json.dumps(...)` в docstring `policy.py:160`. Это корректная защита на нашей
стороне, но **не** доказательство того, что MinIO принимает такой параметр и именно
так его интерпретирует. В дефолтном пути (fallback) этот вопрос не блокирует:
`Policy` не отправляется вообще.

### 3. Policy variables `${aws:username}` / `${jwt:<claim>}` и `StringLike` по `s3:prefix` для `ListBucket`

**НЕ ПОДТВЕРЖДЕНО.** Страница policy-based access control (`policy-based-access-control.html`)
упала с `TypeError: fetch failed`; подтверждения ни по набору переменных, ни по
подстановке в `Resource`/`Condition`, ни по допустимости `StringLike` по `s3:prefix`
в `ListBucket` нет.

Практический вывод (уже заложен в коде и **не требует** этого подтверждения):
`policy.py` намеренно **не использует** policy variables — «динамизм достигается явной
генерацией политики под конкретную пару `(company_id, plugin_id)` на стороне доверенной
платформы» (`policy.py:12-23`), а `ListBucket` отделён в собственный Statement с
`Condition: {"StringLike": {"s3:prefix": ["module-<plugin>/*"]}}` (`policy.py:100-111`).
**Важно для родителя:** это Condition остаётся в политики и при fallback, поэтому его
поддержка конкретной версией MinIO всё равно подлежит ручной проверке на Verify Dev
(чек-лист, п. 5) — просто теперь она влияет на presigned-подписанта, а не на STS-сессию.
При любом сомнении безопасная альтернатива — не полагаться на `StringLike`, а ограничить
подписанта политикой с литеральным `Resource: arn:aws:s3:::tenant-<co>/module-<p>/*`
и запретить `ListBucket` вне доверенного `list_prefix()` (`ports.py:125-132`).

### 4. Настройка STS: `MINIO_IDENTITY_OPENID_CONFIG_URL`, LDAP или встроенный `AssumeRole` с root-кредов; что нужно нам

**НЕ ПОДТВЕРЖДЕНО**, но по локальному манифесту установлен важный факт:
в `infra/k3s/base/platform/minio.yaml:88-103` заданы **только** root-креды,
`MINIO_UPDATE=off`, `MINIO_BROWSER=on`. Нет ни `MINIO_IDENTITY_OPENID_CONFIG_URL`,
ни LDAP-конфигурации, ни какого-либо внешнего IdP, ни отдельного STS-пользователя,
ни `mc admin policy` для STS-роли (init-Job `minio.yaml:200-232` создаёт только
бакет `agentscale`, пользователя `agentscale-api` и политику `agentscale-api-policy`
с правами на весь бакет, `minio-api-policy.json:6-13`).

Наш сценарий — «платформа выдаёт short-lived creds плагину **без внешнего IdP**» —
таким образом упирается в неподтверждённый вопрос: существует ли встроенный
`AssumeRole` от root-кредов без OIDC/LDAP, и не означала бы такая схема передачу
плагину производных от root ключей. Документально это не проверено. Это отдельный рычаг
риска (см. «Риски» → «Отсутствие IdP»), и он ещё одна причина, по которой дефолтом
выбран путь **без выдачи creds вообще**: scoped presigned подписывается доверенной
платформой, плагин получает только URL.

### 5. Ограничения TTL (по умолчанию ≤1ч, min/max) и формат ответа (`AccessKeyId`, `SecretAccessKey`, `SessionToken`, `Expiration`)

**НЕ ПОДТВЕРЖДЕНО.** Официальные границы `DurationSeconds`, дефолт, минимальное и
максимальное значения, а также точный XML-формат ответа `AssumeRole` не получены.

Что заложено у нас (не является границами MinIO): `ports.py:25-30` —
`MIN_TTL_SECONDS=60`, `MAX_TTL_SECONDS=3600`, `DEFAULT_TTL_SECONDS=900` для
presigned через `validate_ttl()`; отдельный STS contract использует
`MIN_STS_TTL_SECONDS=900`, `MAX_STS_TTL_SECONDS=3600` и `validate_sts_ttl()`.
Оба отклоняют выход за диапазон вместо клампа. As-built
`minio_store.py:190,206` использует `ExpiresIn=max(60, int(ttl_seconds))` —
**верхней границы нет**, и это одна из причин, почему v4 вводит
`MAX_TTL_SECONDS=3600`. Для fallback это ограничение уже применимо к
`presign()` (`service.py:493` → `validate_ttl`).

**Breaking STS-only contract migration (2026-11-10):** real disposable matrix
показала `DurationSeconds=60` → HTTP 400 `InvalidParameterValue` и для admin, и
для root, а `DurationSeconds=900` → HTTP 200 с полными temporary credentials и
`EXPIRES_IN=900`. Поэтому STS переведён на 900..3600 с отдельным typed
`validate_sts_ttl()` на service и adapter границах; presign сохраняет 60..3600.
Обычная botocore model validation восстановлена, `parameter_validation=False`
не используется. Probe запрашивает 900 и отбрасывает credentials. Отдельная
canary-only session policy разрешает unconditioned `s3:ListBucket` только на
dedicated configured probe bucket (это авторизует `HeadBucket`) и
`s3:GetObject` только на fixed canary key; tenant data, Put/Delete и другие
buckets не входят в canary policy. Основные tenant scoped credentials не
получают canary access или unscoped ListBucket; tenant List без prefix denied.
Точный HTTP 403 с кодом `AccessDenied` либо numeric `403` является denial,
missing bucket/key с реальным 403/404 остаётся unwired. Финальный disposable
acceptance: **29 collected / 29 passed / 0 skipped** ([JUnit](../../reports/module-audit/minio/mpa-sts900-20261110t070710z-3a2675d3/minio-plugin-acceptance-junit.xml),
[summary](../../reports/module-audit/minio/mpa-sts900-20261110t070710z-3a2675d3/summary.json)); production/live остаётся unverified.
Проверка фактического `X-Amz-Expires`/`X-Amz-Signature` и (при будущем включении STS)
полей ответа вынесена в чек-лист, п. 6.

### 6. Применим ли STS без внешнего IdP; точный fallback и изменения в `ports.py` / `service.py`

**НЕ ПОДТВЕРЖДЕНО** (применимость STS) → **ДА, fallback выбран и является решением**.

**Fallback:** `scoped presigned URL` с IAM-политикой на префикс
`tenant-<company>/module-<plugin>/*`, без выдачи плагину постоянных или short-lived
credentials. Конкретика:

- Платформа при `grant()` создаёт/обновляет `tenant-<company>` и **привязывает**
  сгенерированную `bucket_policy(company_id, plugin_id)` к тому *principal*, которым
  она подписывает URL. Ключевой момент: presigned URL наследует права **подписанта**,
  поэтому политика на префикс должна быть у подписывающего пользователя, а не просто
  «лежать рядом с бакетом». As-built `agentscale-api` с `agentscale/*` для этого
  **не годится** (`minio-api-policy.json`) — нужен отдельный scoped principal или
  отдельная политика на `tenant-<co>/module-<p>/*`.
- Плагин вызывает capability `objects` и получает только: `presign_get` / `presign_put`
  URL на уже нормализованный ключ, `list` (через доверенный `BucketAdminPort.list_prefix`),
  `usage`. Ни `bytes`, ни creds, ни `session_token` в контракте.
- `_normalize_key()` (`service.py:44-112`) уже перестраивает ключ из доверенных
  `(company_id, plugin_id)` и отсекает `../`, чужой `module-*`/`tenant-*`, схемы и
  wildcard — это и есть граница изоляции при presigned.
- TTL 60..3600 (дефолт 900), аудит `plugin_object_access` с `key_digest` (без raw URL
  и без подписи), `strict=True` на allow-событиях — уже реализовано в `service.py`.
- Для внешних клиентов сохраняется `proxy=1` / переписывание host (§5.4, строка 526).

**Что меняется в `objects/ports.py` — ТОЛЬКО описание, правок не вносилось:**

1. `PresignPort` (`ports.py:165-173`) де-факто становится **основным** контрактом
   выдачи доступа. В его docstring зафиксировать: подпись выполняется **только**
   доверенной платформой под scoped principal с политикой на `module-<plugin>/*`;
   запрещено подписывать root-ключами без политики; URL содержит in-cluster host и
   для внешних клиентов требует `proxy=1`/переписи host.
2. `StsPort` + `ScopedCredentials` (`ports.py:63-79,143-161`) **не удалять**, а пометить
   как **opt-in за `PLUGIN_MINIO_STS_ENABLED`**: контракт и unit-тесты к нему уже
   написаны, удаление сломает тесты и закроет будущую оптимизацию. В дефолтной
   сборке адаптер `StsPort` не подключается (`sts=None`).
3. `validate_ttl` / константы оставить едиными для presigned (уже так).
4. Опционально (на усмотрение родителя): расширить `ObjectMethod` и `PresignPort`
   операциями `delete` / `list`, если плагину нужен не только get/put; сейчас удаление
   и листинг идут через доверенный `BucketAdminPort` — это безопаснее и достаточно.
5. В `BucketAdminPort.ensure_bucket(..., policy=...)` уточнить docstring: политика
   ограничивает **подписанта**, а не «бакет вообще».

**Что меняется в `objects/service.py` — ТОЛЬКО описание, правок не вносилось:**

1. `PluginObjectStore.__init__` (`service.py:127-145`): в режиме fallback `presigner`
   должен быть **обязательно сконфигурирован**, а `sts=None` — норма, а не «частично
   собранный» сервис. Предлагается явный режим `mode="presigned" | "sts"` (или чтение
   `PLUGIN_MINIO_STS_ENABLED`) с проверкой на старте, что выбран ровно один путь и что
   в `presigned` подключён именно scoped principal. Сейчас при `presigner is None`
   `presign()` уже fail-closed → `BucketUnavailable` (`service.py:519-532`), т.е. хуже
   не станет; меняется только конфигурационная обязательность.
2. `issue_credentials()` (`service.py:354-470`) **выводится из дефолтного capability-контракта**
   `objects`: плагин не должен даже видеть метод, который «иногда» вернул бы creds.
   Метод остаётся как opt-in за флагом; при `sts=None` он уже возвращает
   `StsUnavailable` (503) и никогда root-креды (`service.py:388-401`) — это корректно,
   нужно лишь не экспонировать его в route `/capabilities/v1/objects/*` по умолчанию.
3. `presign()` (`service.py:475-583`) становится основным путём. Дополнить:
   (а) событие аудита полем `mode="presigned"` / `sts_enabled=false`;
   (б) явный запрет писать в аудит полный URL или `X-Amz-Signature` (сейчас в событии
   только `key_digest` — оставить как есть); (в) проверку, что адаптер-подписант
   сконфигурирован на scoped principal, а не на root.
4. `grant()` (`service.py:210-250`): оставить вызов `ensure_bucket(policy=bucket_policy(...))`,
   но уточнить в docstring, что политика применяется к подписывающему пользователю —
   иначе «IAM-политика на префикс» есть в коде, но не действует на подпись.
5. `revoke()` (`service.py:252-272`): переформулировать reason — сейчас написано
   «STS-сессии не продлеваются», что при fallback неверно. Честная семантика:
   **уже выданные presigned URL не отзываются мгновенно** и живут до `X-Amz-Expires`
   (≤3600 с); отзыв = деактивация `CapabilityGrant` + запрет выдачи новых URL;
   при необходимости мгновенного отзыва — ротация ключа подписанта. Это надо явно
   зафиксировать в дизайне (см. «Риски»).
6. `_normalize_key`, `_is_subset`, аудит, `list`, `usage`, `set_quota`, `uninstall` —
   **менять не требуется**: они уже работают на явном префиксе и не зависят от STS.

---

## Вердикт для v4 (РЕШЕНИЕ, не открытый вопрос)

> **Использовать `scoped presigned URL` с IAM-политикой на префикс `module-<plugin>/*`.**
> **Не выдавать плагину ни постоянных, ни short-lived credentials.**
> **`PLUGIN_MINIO_STS_ENABLED=false` — дефолт.** MinIO STS `AssumeRole` остаётся
> **опциональной оптимизацией**, включаемой только после успешной ручной проверки
> всех 8 пунктов на Verify Dev. Блокер §5.1 плана и вопрос §12.8 дизайна считаются
> **закрытыми через выбор безопасного пути по умолчанию**.

Обоснование: дизайн v4 и план миграции **сами** предписывают этот исход
(`plugin-architecture-complete.md:903`, `plugin-migration-plan.md:146` — «при
недоступности — scoped presigned»), as-built presigned уже работают
(`minio_store.py:186-207`), а весь новый код `objects/*` уже реализует жёсткую
нормализацию ключа, TTL 60..3600 и fail-closed аудит. То есть fallback не требует
нового механизма — он требует лишь **смены дефолта** и уточнения формулировок.
Дополнительно: в манифесте нет внешнего IdP (`minio.yaml:88-103`), а as-built service
user имеет права на весь бакет (`minio-api-policy.json`) — то есть STS-путь потребовал
бы ещё и нетривиальной перестройки IAM, тогда как presigned укладывается в текущий код.

**Что именно меняется в плане/дизайне — вносит родитель (здесь не правлено):**

| Где | Было | Стало |
|---|---|---|
| `docs/migration/plugin-migration-plan.md` §5 п.1 | «Подтвердить MinIO STS … при недоступности — scoped presigned» (открытый блокер) | блокер **закрыт**: «Миграция идёт на scoped presigned; STS — opt-in за `PLUGIN_MINIO_STS_ENABLED` после Verify Dev. См. `docs/migration/minio-sts-verification.md`». Оставить в списке только п.2/п.3 |
| `plugin-migration-plan.md` §3 F3 | «bucket-per-tenant + STS session policy / scoped presigned» | «bucket-per-tenant + **scoped presigned по умолчанию** (IAM-политика на `module-<plugin>/*`); STS за флагом» |
| `plugin-migration-plan.md` §1 F3, критерий готовности | «роль не видит чужую БД и чужой bucket» | добавить «presigned URL не даёт доступ к чужому bucket/префиксу» |
| `plugin-architecture-complete.md` §5.4, строка 517–518 | «префикс + MinIO STS `AssumeRole` session policy с `${aws:username}`» / «short-lived STS creds / scoped presigned» | «bucket-per-tenant + **scoped presigned**, политика на `module-<plugin>/*`, **без выдачи creds плагину**; `${aws:username}` / `${jwt:*}` не используются (не подтверждены)»; STS — сноска «опционально, за фича-флагом» |
| `plugin-architecture-complete.md` §4.3, строка `objects` | «short-lived scoped credentials / presigned, bucket-per-tenant» | «scoped presigned (дефолт), bucket-per-tenant; STS — opt-in» |
| `plugin-architecture-complete.md` §5.4, строка 521 | «:9000 разрешён только plugin-namespace» | оставить, уточнить: egress нужен и для presigned (плагин ходит в MinIO напрямую) |
| `plugin-architecture-complete.md` §11 №19 | «STS creds плагина не видят объекты чужого bucket/префикса» | «**Scoped presigned URL** плагина не даёт доступ к объектам чужого bucket/префикса; попытка подменить ключ → `ObjectAccessDenied`» |
| `plugin-architecture-complete.md` §12 п.8 | открытый вопрос | **закрыт**: «Принят scoped presigned; STS не подтверждён официальной документацией (6/6 попыток), оставлен opt-in. См. отчёт» |
| `plugin-architecture-complete.md` §13.3, §14 п.4, финал | «bucket-per-tenant + STS/presigned», «блокирующее условие — подтверждение MinIO STS» | «bucket-per-tenant + scoped presigned»; блокирующее условие **снято**, вместо него — «8 пунктов Verify Dev для opt-in STS» |
| Конфигурация (новая, родитель) | — | `PLUGIN_MINIO_STS_ENABLED=false` как дефолт; при `true` — подключать `StsPort` и разрешать `issue_credentials` |
| `policy.py` / `ports.py` / `service.py` | см. раздел «Ответы», п.6 | правки **не вносились**; описание изменений выше — спецификация для родителя/задачи 06 |

Примечание: `policy.py` уже не использует policy variables и генерирует явные литералы,
поэтому **неподтверждённость `${aws:username}` / `${jwt:<claim>}` не требует правки
кода** — требует лишь снятия упоминания `${aws:username}` из текста §5.4, чтобы дизайн
не ссылался на неподтверждённый механизм.

---

## Риски

1. **Версия образа `RELEASE.2024-10-02T17-50-41Z` (`minio.yaml:86`).** Документация на
   `min.io` описывает текущую/последнюю версию, а не этот релиз 2024-10-02; даже при
   доступной сети вывод «STS поддерживается» нельзя было бы автоматически перенести на
   закреплённый образ без сверки release notes. Образ — **приватное зеркало** в GHCR
   (`ghcr.io/ne-tort/minio`, `imagePullSecrets: ghcr-pull`, `minio.yaml:80-86`), т.е.
   тег не гарантирует побайтового совпадения с upstream. Плюс `mc` в init-Job — другой
  _release_ (`RELEASE.2024-10-08T09-37-26Z`, `minio.yaml:174`): при расхождении CLI и
   сервера поведение IAM-команд надо проверять, а не предполагать. **Митигация:** п.1
   чек-листа, пин по digest при любой доработке образа.

2. **Отсутствие внешнего IdP.** В `minio.yaml` нет `MINIO_IDENTITY_OPENID_CONFIG_URL`,
   нет LDAP, нет OIDC-провайдера; наш сценарий «платформа выдаёт short-lived creds
   плагину без внешнего IdP» документально не подтверждён и, если реализован через
   root-креды, создаёт цепочку доверия к root-ключам платформы. **Митигация:**
   дефолт не выдаёт creds вообще; STS включается только после п.2 и п.4 чек-листа.
   Дополнительно: as-built `minio-api-policy.json` даёт service user весь
   `agentscale/*` — для scoped подписи нужен **отдельный пользователь/политика**, иначе
   presigned URL унаследует слишком широкие права и «политика на префикс» останется
   только на бумаге. Это самый важный пункт при реализации адаптера.

3. **Egress плагина к `:9000`.** §6.2 открывает `:9000` только для `plugin-namespace`
   (`plugin-architecture-complete.md:614-621`, лейбл
   `app.kubernetes.io/name: agentscale-minio`). При presigned плагин **обязан** иметь
   прямой сетевой путь к MinIO, иначе URL бесполезен. Риски: несовпадение лейбла
   селектора (в дизайне прямо отмечено, что `app: minio` не сработает), отсутствие
   NP для нового namespace плагинов, и обратная ошибка — случайное открытие `:9000`
   агентским Pod'ам. **Митигация:** п.7 чек-листа (две пробы: из plugin-пода
   разрешено, из agent-пода запрещено).

4. **In-cluster DNS для presigned.** `PresignPort` подписывает URL на
   `http://agentscale-minio:9000` (Service из `minio.yaml:33-50`). Это имя резолвится
   **только внутри кластера**: внешний клиент/браузер получит нерезолвящийся host,
   а переписывание host после подписи **инвалидирует `X-Amz-Signature`** (подпись
   считается по каноническому запросу, включая Host). Дизайн уже держит `proxy=1` для
   внешних клиентов (`plugin-architecture-complete.md:526`) — надо гарантировать, что
   в контракте `objects` это не «опция по умолчанию забыта», а явный режим, и что
   плагин (in-cluster) и внешний клиент получают **разные** формы URL. Связанный риск:
   утечка подписи через Referer/access-логи/аудит — в `service.py` сейчас пишется
   только `key_digest`, это надо сохранить и явно запретить логировать URL целиком.
   **Митигация:** п.8 чек-листа.

5. **Отзыв доступа при presigned (новый, вытекает из выбора fallback).** `revoke()`
   деактивирует `CapabilityGrant`, но уже выданный URL живёт до `X-Amz-Expires`
   (≤3600 с, дефолт 900) — мгновенного отзыва нет, в отличие от STS-сессии. При
   коротком TTL окно ≤15 мин; при необходимости «мгновенно» — ротация ключа
   подписанта. Требует явной формулировки в §5.5 / ADR и правки reason в
   `service.py:266` (сейчас там про «STS-сессии не продлеваются»).

6. **Сам отчёт основан на недоступности сети.** Статус «НЕ ПОДТВЕРЖДЕНО» — не «НЕТ»:
   если позже документация откроется и окажется, что STS применим без IdP, родитель
   может включить оптимизацию, не переделывая контракты (`StsPort` сохранён).
   Обратное тоже верно: **не** считать отсутствие ошибки сети доказательством
   поддержки. Все выводы о поведении MinIO подлежат живой проверке ниже.

---

## Чек-лист ручной проверки на Verify Dev (8 пунктов)

Сеть недоступна → всё ниже проверяется **вживую на dev-кластере** до любого включения
`PLUGIN_MINIO_STS_ENABLED=true`. В этой сессии **ничего не запускалось**; пункты —
спецификация для Verify Dev. Работаем только с dev (`agentscale-dev`), prod не трогаем.

1. **Версия и образ.** Сверить развёрнутый образ с `minio.yaml:86`: запросить у пода
   `agentscale-minio` версию сервера и digest (`kubectl -n agentscale-dev get sts
   agentscale-minio -o jsonpath='{..image}'`, `mc admin info local`, `minio --version`
   в контейнере). Зафиксировать, что это именно `RELEASE.2024-10-02T17-50-41Z`, и
   отдельно отметить версию `mc` из init-Job. Сверить release notes **этой** версии, а
   не `master`-документацию.

2. **Наличие STS без IdP.** Из пода в namespace плагинов выполнить пробный
   `AssumeRole` на `http://agentscale-minio:9000` и проверить `mc admin config get
   local identity_openid` / `identity_ldap`. Ожидаемая фиксация: настроен ли STS вообще,
   нужен ли внешний IdP, и **не возвращает ли вызов root/статические ключи**. Без
   успешного ответа с `SessionToken` STS считать неприменимым.

3. **Session policy = подмножество.** Только если п.2 дал рабочий STS: передать
   `Policy=json.dumps(session_policy(...))`, затем попытаться сузить/расширить её и
   проверить, что эффективные права равны пересечению и **не превышают**
   `module-<plugin>/*`; отдельно убедиться, что creds не видят `tenant-<other>`.

4. **Policy variables.** На этой версии проверить подстановку `${aws:username}` и
   `${jwt:<claim>}` в `Resource`/`Condition`. При любом расхождении — оставить явные
   литералы из `policy.py` (уже так) и зафиксировать отказ от variables в дизайне.
   Для нашего случая без IdP `${jwt:*}` заведомо не имеет источника claim'ов.

5. **`StringLike` по `s3:prefix` для `ListBucket`.** Под scoped principal проверить:
   листинг со своим `module-<plugin>/` разрешён; с пустым/чужим `module-<other>/`
   или чужим бакетом — запрещён. Если Condition не работает на этой версии —
   убрать reliance на него и положиться на литеральный `Resource` + доверенный
   `BucketAdminPort.list_prefix()`.

6. **TTL и формат ответа.** Для presigned (обязательно): проверить `X-Amz-Expires` ≤
   3600 и ≥60, дефолт 900, отказ `validate_ttl` на 59/3601, наличие
   `X-Amz-Date`/`X-Amz-Signature`/`X-Amz-Credential`, и что URL после истечения даёт
   отказ. Для STS (только при п.2=ДА): проверить границы `DurationSeconds`, дефолт ≤1ч,
   min/max и поля `AccessKeyId`, `SecretAccessKey`, `SessionToken`, `Expiration` в
   XML-ответе. Заодно зафиксировать семантику `revoke` для уже выданных URL (риск №5).

7. **Egress `:9000`.** Проверить `NetworkPolicy plugin-egress` на совпадение лейбла
   `app.kubernetes.io/name: agentscale-minio`, затем две пробы `curl
   http://agentscale-minio:9000/minio/health/live`: из plugin-пода → успех, из
   agent-пода → отказ. Убедиться, что платформенный `prodavan`/PG в egress не попал.

8. **In-cluster DNS и утечка подписи.** Сгенерировать presigned URL и проверить его
   (а) из пода плагина, (б) с хоста/внешнего клиента — `agentscale-minio:9000` не
   резолвится; убедиться, что для внешних клиентов работает `proxy=1` или host
   переписан **до** подписи (иначе `SignatureDoesNotMatch`). Проверить, что ни URL,
   ни `X-Amz-Signature`, ни creds не попали в аудит `plugin_object_access`, логи
   gateway, Referer и access-логи; в событии допустим только `key_digest`.

**Критерий включения STS:** все 8 пунктов выполнены, п.2–п.6 дали однозначное «ДА» для
`RELEASE.2024-10-02T17-50-41Z`, и родитель явно выставил
`PLUGIN_MINIO_STS_ENABLED=true`. До этого дефолт — **scoped presigned**.

---

**Итог:** блокер «MinIO STS не подтверждён официальной документацией» снят **выбором
fallback**, а не получением подтверждения. Код, тесты, манифесты и git в этой сессии
не изменялись и не запускались; создан только этот документ.
