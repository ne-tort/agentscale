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
| `apps/api/src/prodavan/infrastructure/plugins/minio/config.py` | `MinioPluginConfig` из 5 env, fail-closed, запрет root/as-built ключей. |
| `apps/api/src/prodavan/infrastructure/plugins/minio/adapters.py` | `MinioBucketAdmin`, `MinioSts`, `MinioPresigner`, `MinioAuditAdapter`, `merge_bucket_policies`, `QuotaAdminPort`, `UnavailableQuotaAdmin`, `InMemoryPluginAuditSink`. |
| `apps/api/src/prodavan/infrastructure/plugins/minio/__init__.py` | Фабрика `build_plugin_object_store(config) -> PluginObjectStore`. |
| `apps/api/src/prodavan/infrastructure/plugins/minio/README.md` | Этот отчёт + пометки о требующем подтверждения. |
| `infra/k3s/base/platform/plugin-minio-policy.json` | **НОВАЯ** IAM-политика plugin-admin: только `tenant-*`, явный `Deny` на `arn:aws:s3:::agentscale/*`. |
| `infra/k3s/base/platform/plugin-buckets-init.yaml` | **НОВЫЙ** Sync-хук Job: service user `plugin-object-admin`, политика, включение квот, БЕЗ создания бакетов. |
| `apps/api/tests/unit/plugins/minio/**` | Unit-тесты (написаны, НЕ запускались) + README тестов. |

`minio-api-policy.json`, `minio.yaml`, `kustomization.yaml`, `00-project.yaml`,
`configmap.yaml` и весь код задачи 06 **НЕ редактировались**.

## Ключевые решения

1. **Fail-closed конфигурация.** Читаются ТОЛЬКО `PLUGIN_MINIO_ENDPOINT`,
   `PLUGIN_MINIO_ADMIN_ACCESS_KEY`, `PLUGIN_MINIO_ADMIN_SECRET_KEY`,
   `PLUGIN_MINIO_STS_ENABLED`, `PLUGIN_MINIO_REGION`. Нет admin-кредов →
   `PluginMinioConfigError` (наследник `BucketUnavailable`). **Никакого**
   фолбэка на `MINIO_ROOT_*`, `S3_*`, `S3_BUCKET=agentscale` или dev-значения из
   `configmap.yaml`. Явно запрещены access key `agentscale`/`agentscale-api`/
   `root`/`minio`/`minioadmin`. `repr` конфигурации скрывает секреты.
   Неизвестное значение `PLUGIN_MINIO_STS_ENABLED` = ошибка, а не `False`.

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

5. **STS: `StsUnavailable`, никогда root-креды.** `MinioSts` по умолчанию
   `sts_available=False` и отвечает `StsUnavailable` ДО сетевого вызова.
   Проверяет, что `session_policy` ровно равна канонической
   `iam.session_policy(company, plugin)`; из ответа `AssumeRole` берёт ТОЛЬКО
   `AccessKeyId`/`SecretAccessKey`/`SessionToken`; запрещает совпадение с
   admin-ключами и известные root/API-идентификаторы; `expires_at` ограничен
   `now + ttl` (без «продления»). Никаких policy variables
   (`${aws:username}`/`${jwt:*}`) — они не подтверждены (§12.8).
   При отсутствии STS единственный путь — **scoped presigned** (`MinioPresigner`),
   короткий TTL, без выдачи постоянных кредов.

6. **Presign.** TTL через единый `ports.validate_ttl` (60..3600, `InvalidTtl`,
   НЕ `max(60, ...)` как в as-built `minio_store.py`). Подписывается только
   нормализованный ключ в своём бакете; проверяется, что URL указывает на
   сконфигурированный endpoint (иначе «scoped» увёл бы плагина на чужой хост).

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
    `minio.yaml`): пересоздаёт политику идемпотентно, создаёт
    `plugin-object-admin`, аттачит политику, smoke-проверяет, что у нового
    пользователя НЕТ доступа к `agentscale`, проверяет поддержку hard quota и
    выставляет её на уже существующие `tenant-*`. **`mc mb` здесь запрещён** —
    бакеты создаются при grant. Kinds: `Secret` и `batch/v1 Job` (оба в whitelist
    `00-project.yaml`), ConfigMap монтируется именем
    `agentscale-minio-plugin-policy`.

## ⚠ ТРЕБУЕТ ПОДТВЕРЖДЕНИЯ (официальная документация MinIO недоступна)

Помечено в коде (`adapters.py` docstring, `MinioSts`, `config.py`,
`__init__.py`, шапка `plugin-buckets-init.yaml`) и здесь:

1. **MinIO STS `AssumeRole` + session policy (`Policy=json.dumps(...)`,
   `DurationSeconds`) и `StringLike` по `s3:prefix`** для образа
   `ghcr.io/ne-tort/minio:RELEASE.2024-10-02T17-50-41Z` (v4 §12.8, план §5.1,
   `policy.py` docstring). В сессии официальная документация недоступна.
   ⇒ `PLUGIN_MINIO_STS_ENABLED=false` по умолчанию, `MinioSts` fail-closed,
   **используется ТОЛЬКО scoped presigned**, без выдачи постоянных кредов.
   Включать STS только после подтверждения документацией + integration-проверки
   критерия §11.19.
2. **`mc admin bucket quota --hard` / MinIO Admin API `SetBucketQuota`** — точный
   синтаксис и поведение этим образом тоже не подтверждены. Job проверяет
   наличие подкоманды и падает, если её нет; `QuotaAdminPort` остаётся
   инжектируемым контрактом для родителя.
3. AWS/MinIO-семантика IAM `Deny` на фоне wildcard-Allow для service user и
   корректность ARN `tenant-*/module-*` — проверяется smoke-тестом в Job
   (`mc ls pluginadmin/agentscale` должен упасть).

## Что нужно родителю (F11-интеграция, вне моей зоны)

1. `infra/k3s/base/platform/kustomization.yaml`:
   `resources: - plugin-buckets-init.yaml` и
   `configMapGenerator: - name: agentscale-minio-plugin-policy / files: [plugin-minio-policy.json]`
   (`disableNameSuffixHash: true` уже стоит).
2. API Deployment: env `PLUGIN_MINIO_*` из Secret `agentscale-minio-plugin-admin`
   (в dev — plaintext как в `minio.yaml`; для shared-кластера заменить на
   SealedSecret/внешний Secret).
3. Реализовать `AuditSinkPort` (append-only таблица/журнал) и `QuotaAdminPort`
   (mc/Admin API) и передать их в `build_plugin_object_store`.
4. Каталог `infrastructure/plugins/` новый: `__init__.py` в нём НЕ создавался
   (правило «только свои каталоги», работает как PEP 420 namespace package).
   Если tooling потребует маркер — добавить пустой файл самому.
5. Не добавлять `ListAllMyBuckets`/`Resource: "*"` в plugin-политику без
   пересмотра изоляции.

**Никакие тесты, сборки, линт, компиляция, форматирование, git, kubectl, mc
не запускались.** Финальный отчёт — только родителю через `send_message`.
