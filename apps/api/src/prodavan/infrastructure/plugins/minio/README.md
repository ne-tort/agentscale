# MinIO-адаптеры объектной capability плагинов (F3, задача «порты задачи 06»)

**Статус:** реализовано. **Тесты / сборки / линт / компиляция / форматирование /
git (status/diff/add/commit/push) / kubectl / mc НЕ запускались** — verification
freeze (`docs/migration/plugin-migration-plan.md` §0.2, v4 §11). Тесты
НАПИСАНЫ, но не исполнялись.

## Прочитано (обязательные источники)

- `docs/ARCHITECTURE/plugin-architecture-complete.md` v4 — §1 (таблица замен),
  §4.3/§4.4/§4.5, **§5.4**, §5.5, §6.2, **§11 кр. 11/15/19**, §12.8,
  **§13.3**, §14.
- `docs/migration/plugin-migration-plan.md` — §0 (правила субагентов), §2 (владение),
  §3 F3, §5 (открытые блокеры).
- Код задачи 06 (ОБЯЗАТЕЛЬНО, реализую его порты):
  `apps/api/src/prodavan/application/plugins/objects/{ports,service,policy,errors,__init__}.py`
  — контракты `BucketAdminPort`, `StsPort`, `PresignPort`, `AuditPort`,
  `PluginObjectStore`, `ScopedCredentials`, `ObjectInfo`, `validate_ttl`,
  генераторы `bucket_name` / `module_prefix` / `bucket_policy` / `session_policy`.
- `infra/k3s/base/platform/minio.yaml` (образ MinIO, образ mc, Sync-хук wave 9,
  Secret `agentscale-minio`), `minio-api-policy.json` (as-built `agentscale/*`),
  `infra/k3s/base/platform/kustomization.yaml`,
  `infra/k3s/base/agentscale-api/configmap.yaml`,
  `infra/argocd/apps/00-project.yaml` (whitelist kinds).
- `apps/api/src/prodavan/infrastructure/files/minio_store.py` — boto3-паттерн
  (`Config(signature_version="s3v4")`, `request_checksum_calculation="when_required"`,
  `generate_presigned_url`, пагинатор `list_objects_v2`, fallback per-object delete).
- Тесты задачи 06 (`tests/unit/plugins/objects/conftest.py`, `test_service.py`) —
  стиль fakes и ожидания, чтобы не расходиться с родительским контрактом.

## Файлы (строго свои каталоги, чужие не тронуты)

| Файл | Содержание |
|---|---|
| `apps/api/src/prodavan/infrastructure/plugins/minio/config.py` | `MinioPluginConfig` из env (admin + ОТДЕЛЬНЫЙ presigner + public endpoint), fail-closed, запрет root/as-built ключей. |
| `apps/api/src/prodavan/infrastructure/plugins/minio/adapters.py` | `MinioBucketAdmin`, `MinioSts`, `MinioPresigner`, `MinioAuditAdapter`, `merge_bucket_policies`, `QuotaAdminPort`, `UnavailableQuotaAdmin`, `InMemoryPluginAuditSink`. |
| `apps/api/src/prodavan/infrastructure/plugins/minio/__init__.py` | Фабрика `build_plugin_object_store(config) -> PluginObjectStore`. |
| `apps/api/src/prodavan/infrastructure/plugins/minio/README.md` | Этот отчёт + пометки о требующем подтверждения. |
| `infra/k3s/base/platform/plugin-minio-policy.json` | **НОВАЯ** IAM-политика plugin-admin: только `tenant-*`, явный `Deny` на `arn:aws:s3:::agentscale/*`. |
| `infra/k3s/base/platform/plugin-buckets-init.yaml` | **НОВЫЙ** Sync-хук Job: `plugin-object-admin` + отдельный `plugin-object-presigner` с узкой политикой, включение квот, БЕЗ создания бакетов. |
| `apps/api/tests/unit/plugins/minio/**` | Unit-тесты (написаны, НЕ запускались) + README тестов. |

`minio-api-policy.json`, `minio.yaml`, `kustomization.yaml`, `00-project.yaml`,
`configmap.yaml` и весь код задачи 06 **НЕ редактировались**.

## Ключевые решения

1. **Fail-closed конфигурация.** Читаются ТОЛЬКО `PLUGIN_MINIO_ENDPOINT`,
   `PLUGIN_MINIO_ADMIN_ACCESS_KEY`, `PLUGIN_MINIO_ADMIN_SECRET_KEY`,
   `PLUGIN_MINIO_PRESIGN_ACCESS_KEY`, `PLUGIN_MINIO_PRESIGN_SECRET_KEY`,
   `PLUGIN_MINIO_PUBLIC_ENDPOINT` (необязателен), `PLUGIN_MINIO_STS_ENABLED`,
   `PLUGIN_MINIO_REGION`. Нет admin ИЛИ presigner-кредов →
   `PluginMinioConfigError` (наследник `BucketUnavailable`). **Никакого**
   фолбэка на `MINIO_ROOT_*`, `S3_*`, `S3_BUCKET=agentscale` или dev-значения из
   `configmap.yaml`. Явно запрещены access key `agentscale`/`agentscale-api`/
   `root`/`minio`/`minioadmin`; admin и presigner ОБЯЗАНЫ быть разными
   principal. `repr` конфигурации скрывает секреты. Неизвестное значение
   `PLUGIN_MINIO_STS_ENABLED` = ошибка, а не `False`.

2. **МЕРДЖ политик, а не перезапись (главный контракт задачи 06).**
   `ensure_bucket` читает `get_bucket_policy`, объединяет Statement с новой
   `bucket_policy(company, plugin)` и пишет результат. Дедупликация — по
   **каноническому JSON всего Statement**, НЕ по `Sid`:生成器 `policy.py` даёт
   каждому плагину ОДИНАКОВЫЕ `Sid` (`ListOwnModulePrefix`,
   `ReadWriteOwnModuleObjects`), различаются только `Resource`/`Condition` с
   `module-<plugin>/`. Дедуп по `Sid` молча перезаписал бы политику предыдущего
   плагина компании — ровно тот баг, который требует исправить. Повторный grant
   того же плагина идемпотентен; grant второго плагина добавляет новые Statement.
   Чистая функция `merge_bucket_policies` экспортирована и проверяется тестом.
   Перед записью политика валидируется: только `Allow`, только
   `iam.OBJECT_ACTIONS`, только `arn:aws:s3:::tenant-<co>[/module-*]`, запрет
   `Resource: "*"`, запрет `arn:aws:s3:::agentscale/*`.

3. **Ни одного `bytes`.** Ни один метод адаптеров не принимает/не возвращает
   payload. `get_usage`/`list_prefix` оперируют только `Size`/`Key`/`ETag`;
   `delete_prefix` возвращает счётчик; `presign_*` возвращает URL. boto3-вызовы
   вынесены в `asyncio.to_thread` (паттерн `infrastructure/files/manager.py`).

4. **Жёсткий префикс на уровне адаптера тоже.** `_validate_bucket` разрешает
   только каноническое `tenant-<company>` (имя `agentscale` и не-tenant имена
   → `ObjectAccessDenied`). `_normalize_prefix` — ровно `module-<plugin>/`.
   `_normalize_key` перепроверяет ключ (сервис уже нормализовал его, но порт
   «НЕ доверяет входу» по `ports.py`): `../`, `..\`, `//`, `/`, `s3://`, `*`,
   `?`, NUL, `tenant-*`, не-`module-` ключ → `ObjectAccessDenied`. Ключ без
   префикса НЕ «достраивается» адаптером (это обязанность сервиса), а
   отклоняется. `list_prefix` перепроверяет каждый возвращённый ключ.

5. **STS — opt-in, НЕ удалён (вердикт родителя).** Сеть недоступна,
   официальная документация MinIO STS не подтверждена ⇒ по умолчанию
   `PLUGIN_MINIO_STS_ENABLED=false`, **`PresignPort` — ОСНОВНОЙ и обязательный**
   в дефолтном режиме. `StsPort`/`ScopedCredentials` СОХРАНЕНЫ за флагом;
   `issue_credentials` НЕ экспонируется в `/capabilities/v1/objects/*` по
   умолчанию (зона родителя). `MinioSts` при `sts_available=False` отвечает
   `StsUnavailable` ДО сетевого вызова. Проверяет, что `session_policy` ровно
   равна канонической `iam.session_policy(company, plugin)`; из ответа
   `AssumeRole` берёт ТОЛЬКО `AccessKeyId`/`SecretAccessKey`/`SessionToken`;
   запрещает совпадение с admin-ключами и известные root/API-идентификаторы;
   `expires_at` ограничен `now + ttl` (без «продления»). Никаких policy
   variables (`${aws:username}`/`${jwt:*}`) — не подтверждены (§12.8).
   Никаких root-кредов и никаких постоянных кредов service user'а.

5a. **ОТДЕЛЬНЫЙ подписывающий principal (критично).** as-built
   `minio-api-policy.json` даёт `agentscale-api` весь
   `arn:aws:s3:::agentscale/*`; подпись presigned от такого ключа (или от
   admin-ключа) унаследовала бы слишком широкие права — «политика на префикс»
   осталась бы только на бумаге. Создан `plugin-object-presigner` со
   СВОЕЙ узкой политикой: только `GetObject`/`PutObject` на
   `arn:aws:s3:::tenant-*/module-*`, плюс явный `Deny` на `agentscale/*`.
   `MinioPresigner` строит отдельный boto3-клиент из
   `config.presign_access_key`/`presign_secret_key`/`presign_endpoint`;
   фабрика передаёт `presign_client` отдельно от `s3_client`.

5b. **Отзыв в presign-режиме (чужой `service.py` задачи 06 НЕ правлен).** Его
   `revoke()` пишет «STS-сессии не продлеваются» — при fallback это неточно:
   выданные URL живут до `X-Amz-Expires` ≤ 3600 с и **НЕ отзываются
   мгновенно**. Отзыв = запрет НОВЫХ URL + деактивация `CapabilityGrant`;
   мгновенный отзыв — **ротация ключа** `plugin-object-presigner`. Помечено в
   `config.py`, `adapters.py` (`MinioPresigner` docstring), `__init__.py` и
   шапке `plugin-buckets-init.yaml`.

5c. **Внешние клиенты и логи.** Presigned URL и `X-Amz-Signature` НЕ
   логируются и НЕ уходят в аудит (только `key_digest`, §4.5 кр. 11).
   In-cluster `agentscale-minio:9000` не резолвится снаружи ⇒ нужен
   `proxy=1` ИЛИ подпись СРАЗУ на внешний host через
   `PLUGIN_MINIO_PUBLIC_ENDPOINT` (`config.presign_endpoint`); перепись Host
   ПОСЛЕ подписи инвалидирует SigV4. `MinioPresigner` сверяет netloc/scheme
   подписанного URL с `presign_endpoint`.

6. **Presign.** TTL через единый `ports.validate_ttl` (60..3600, `InvalidTtl`,
   НЕ `max(60, ...)` как в as-built `minio_store.py`). Подписывается только
   нормализованный ключ в своём бакете отдельным ключом подписанта; URL
   проверяется на соответствие `presign_endpoint`. Presigner ОБЯЗАТЕЛЕН в
   дефолтном режиме (без STS).

7. **Аудит `plugin_object_access` с redaction.** Белый список канонических
   полей (`_ALLOWED_AUDIT_FIELDS`), `bytes`/вложенные структуры →
   `AuditUnavailable`, подозрительные строки (`X-Amz-Credential`,
   `X-Amz-Signature`, `SessionToken`, `Bearer`, URL, `password`…) → sha256-digest,
   длинный `reason` → digest, неизвестный объект → только имя типа. Append идёт в
   инжектируемый `AuditSinkPort`; отсутствие sink/ошибка append →
   `AuditUnavailable` (сервис задачи 06 с `strict=True` превращает это в deny).
   `InMemoryPluginAuditSink` — только для тестов. В проде фабрика по умолчанию
   НЕ подставляет noop-sink: тихий лог создал бы иллюзию аудита.

8. **Квоты — отдельный порт.** boto3 S3 API не имеет `SetBucketQuota`;
   `set_quota`/`grant` делегируют `QuotaAdminPort`. Без реализации —
   `UnavailableQuotaAdmin` → `BucketUnavailable` (НЕ безлимит, НЕ счётчик в БД).
   `get_usage` = сумма `Size` (observability, по §5.4 не считается hard limit).

9. **`owns_bucket=True`.** Bucket-per-tenant: `uninstall` удаляет ТОЛЬКО свой
   `tenant-<company>` (критерий §11.15). `delete_prefix` реализован для
   v1-компромисса «префикс + STS», но в этом режиме не вызывается.

10. **Infra.** Новая политика: `Deny s3:*` на `arn:aws:s3:::agentscale` и
    `arn:aws:s3:::agentscale/*`, затем Allow только на `tenant-*` и
    `tenant-*/module-*`, плюс `CreateBucket`/`DeleteBucket`/`Get|PutBucketPolicy`
    (административные действия нужны доверенной платформе, НЕ плагину).
    `ListAllMyBuckets` намеренно НЕ включён: он требует `Resource: "*"` и
    размыл бы гарантию «только tenant-*». Политика НЕ редактирует
    `minio-api-policy.json`.
    Job `plugin-buckets-init` (Sync, wave 9, тот же образ `mc`, что в
    `minio.yaml`): пересоздаёт обе политики идемпотентно, создаёт
    `plugin-object-admin` И `plugin-object-presigner`, аттачит им РАЗНЫЕ
    политики, smoke-проверяет, что у ОБОИХ нет доступа к `agentscale`,
    проверяет поддержку hard quota и выставляет её на уже существующие
    `tenant-*`. **`mc mb` здесь запрещён** — бакеты создаются при grant.
    Kinds: `Secret`, `ConfigMap` и `batch/v1 Job` (все в whitelist
    `00-project.yaml`), ConfigMap `agentscale-minio-plugin-policy` монтируется
    родительским генератором, политика подписанта — инлайновым ConfigMap
    `agentscale-minio-plugin-presign-policy` (чтобы не править общий
    kustomization.yaml).

## ⚠ ТРЕБУЕТ ПОДТВЕРЖДЕНИЯ (официальная документация MinIO недоступна)

Помечено в коде (`adapters.py` docstring, `MinioSts`, `config.py`,
`__init__.py`, шапка `plugin-buckets-init.yaml`) и здесь:

1. **MinIO STS `AssumeRole` + session policy (`Policy=json.dumps(...)`,
   `DurationSeconds`) и `StringLike` по `s3:prefix`** для образа
   `ghcr.io/ne-tort/minio:RELEASE.2024-10-02T17-50-41Z` (v4 §12.8, план §5.1,
   `policy.py` docstring). **Сеть недоступна, документ не подтверждён;
   вердикт родителя: по умолчанию scoped presigned,
   `PLUGIN_MINIO_STS_ENABLED=false`, STS только opt-in после РУЧНОЙ проверки
   на Verify Dev.** `MinioSts` fail-closed, `StsPort`/`ScopedCredentials`
   сохранены за флагом, постоянные креды не выдаются.
2. **`mc admin bucket quota --hard` / MinIO Admin API `SetBucketQuota`** — точный
   синтаксис и поведение этим образом тоже не подтверждены. Job проверяет
   наличие подкоманды и падает, если её нет; `QuotaAdminPort` остаётся
   инжектируемым контрактом для родителя.
3. AWS/MinIO-семантика IAM `Deny` на фоне wildcard-Allow для service user и
   корректность ARN `tenant-*/module-*` — проверяется smoke-тестом в Job
   (`mc ls pluginadmin/agentscale` должен упасть).

## Что нужно родителю (F11-интеграция, вне моей зоны)

1. `infra/k3s/base/platform/kustomization.yaml` — ✅ УЖЕ сделано родителем
   (сверено): `resources: plugin-buckets-init.yaml`, `plugin-topics-init.yaml`,
   `configMapGenerator: agentscale-minio-plugin-policy`. Имена совпадают с
   монтированием в Job.
2. API Deployment — ✅ родитель добавил `secretRef: agentscale-minio-plugin-admin`
   и `PLUGIN_MINIO_STS_ENABLED/REGION/ENDPOINT` в configmap. **ОСТАЛОСЬ:**
   добавить `secretRef: agentscale-minio-plugin-presigner` и env
   `PLUGIN_MINIO_PRESIGN_ACCESS_KEY` / `PLUGIN_MINIO_PRESIGN_SECRET_KEY`
   (секреты НЕ в ConfigMap), а при внешних клиентах —
   `PLUGIN_MINIO_PUBLIC_ENDPOINT` или `proxy=1`.
3. `PLUGIN_MINIO_ADMIN_*` также должны прийти из Secret (не ConfigMap);
   проверить, что `agentscale-minio-plugin-admin` содержит ключи
   `access-key` / `secret-key` ( Job их создаёт ).
4. НЕ экспонировать `issue_credentials` в `/capabilities/v1/objects/*` по
   умолчанию; основной путь — `presign` (вердикт родителя).
5. Реализовать `AuditSinkPort` (append-only таблица/журнал) и `QuotaAdminPort`
   (mc/Admin API) и передать их в `build_plugin_object_store`.
6. Каталог `infrastructure/plugins/` новый: `__init__.py` в нём НЕ создавался
   (правило «только свои каталоги», работает как PEP 420 namespace package).
   Если tooling потребует маркер — добавить пустой файл самому.
7. Не добавлять `ListAllMyBuckets`/`Resource: "*"` в plugin-политику без
   пересмотра изоляции.

**Никакие тесты, сборки, линт, компиляция, форматирование, git, kubectl, mc
не запускались.** Финальный отчёт — только родителю через `send_message`.
