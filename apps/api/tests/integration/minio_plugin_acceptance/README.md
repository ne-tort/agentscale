# Real-MinIO plugin acceptance harness

**Статус: real disposable acceptance выполнен 2026-11-10.** Финальный run
`mpa-sts900-20261110t070710z-3a2675d3`: **29 collected / 29 passed / 0 failed /
0 errors / 0 skipped**. Сохранены [JUnit](../../../../../reports/module-audit/minio/mpa-sts900-20261110t070710z-3a2675d3/minio-plugin-acceptance-junit.xml)
и [summary](../../../../../reports/module-audit/minio/mpa-sts900-20261110t070710z-3a2675d3/summary.json).
Это не production/live acceptance; STOP-SHIP сохраняется.

## Граница безопасности

Harness предназначен только для одноразового изолированного MinIO. Нельзя
направлять его на live, production или shared endpoint. Он не читает настройки
приложения и не включает application/dev/prod feature flags. Адаптеры получают
test-local конфигурацию непосредственно в acceptance fixtures.

- Endpoint и disposable root credentials задаются только через env. В коде нет
  root/service credential fallback или реальных секретов.
- Имена основного, foreign и dedicated canary buckets, двух IAM users и трёх
  IAM policies (tenant/presign/canary) содержат случайный run id. Секреты временных test principals принимаются только из acceptance env и
  не выводятся. Root используется только для создания и удаления этих ресурсов.
- Создаются основной tenant bucket, отдельный foreign bucket и dedicated
  canary bucket с canary-only policy. Фиксированный canary object и foreign
  objects существуют и имеют нулевую длину, поэтому
  NoSuchBucket/NoSuchKey не могут быть приняты за доказательство AccessDenied.
- Cleanup удаляет только успешно созданные этим запуском random-named resources.
  Глобальные list/purge IAM, удаление чужих buckets и flush всего хранилища
  отсутствуют.
- MinIO IAM user/policy lifecycle выполняет проектный mc. S3/STS операции и
  проверяемые пути используют production boto3, MinioPresigner, MinioSts и
  MinioObjectsAdapter. Дочерний mc получает только PATH/SystemRoot,
  MC_CONFIG_DIR и MC_HOST_acceptance, но не ambient application/MinIO/AWS/proxy
  env. Вывод mc и transport logging подавлены; stdout/stderr процесса не
  включаются в ошибки.
- Probe credentials не возвращаются, не логируются и не сохраняются. После
  успешного probe выполняется отдельный AssumeRole, и уже эта новая scoped
  session повторно проверяет разрешённые и запрещённые S3 операции.
- HTTPX использует trust_env=False, follow_redirects=False и bounded timeout.
  Boto3/botocore clients получают явно пустые HTTP/HTTPS/ALL proxy mappings;
  production adapters сохраняются, но их injected clients не наследуют ambient
  proxy. Static/runtime guards используют недоступные sentinel proxies и secrets;
  реальные positive операции обязаны пройти напрямую без их раскрытия.
- Полные presigned URL и значения подписи не попадают в assertion messages или
  логи. Медленный expiry test использует только wall clock, TTL=60 и controlled
  wait до фактического срока из X-Amz-Date/X-Amz-Expires; fake clock нет.

## Точные env names

| Переменная | Обязательность | Назначение |
|---|---|---|
| **MINIO_PLUGIN_ACCEPTANCE_ENDPOINT** | Для любого прогона | Явный HTTP(S) origin одноразового MinIO, без credentials/path/query. Само наличие включает no-skip gate. |
| **MINIO_PLUGIN_ACCEPTANCE_ROOT_ACCESS_KEY** | Для любого прогона | Root access key только этого disposable MinIO. |
| **MINIO_PLUGIN_ACCEPTANCE_ROOT_SECRET_KEY** | Для любого прогона | Root secret key только этого disposable MinIO. |
| **MINIO_PLUGIN_ACCEPTANCE_ADMIN_SECRET_KEY** | Для любого прогона | Secret key случайно именованного отдельного STS/admin principal. |
| **MINIO_PLUGIN_ACCEPTANCE_PRESIGN_SECRET_KEY** | Для любого прогона | Secret key случайно именованного отдельного presigner principal; не совпадает с admin/root. |
| **MINIO_PLUGIN_ACCEPTANCE_MC_BINARY** | Для любого прогона | Путь к mc; используется проектный release **RELEASE.2024-10-08T09-37-26Z**. |
| **MINIO_PLUGIN_ACCEPTANCE_CONFIRM_ISOLATED_TEST_ENV** | Для любого прогона | Должна быть ровно **I_OWN_DISPOSABLE_MINIO_RESOURCES**. |
| **MINIO_PLUGIN_ACCEPTANCE_REGION** | Нет | S3 region; default **us-east-1**. |
| **MINIO_PLUGIN_ACCEPTANCE_REQUIRED** | Рекомендуется для обязательного прогона | Значение **1** запрещает skip при отсутствующих обязательных inputs. |

Никакие **PLUGIN_MINIO_***, application settings, live endpoint или production
secret variables не читаются и не изменяются.

## Disposable non-prod MinIO launch pattern

Ниже только документированный pattern, **не выполненная команда**. Image и args
совпадают с подтверждённым проектным manifest **infra/k3s/base/platform/minio.yaml**:
**ghcr.io/ne-tort/minio:RELEASE.2024-10-02T17-50-41Z**, **server /data**,
**--address :9000**, **--console-address :9001**. Digest не выдумывается и не
добавляется. Для приватного project mirror нужен уже настроенный локальный GHCR
доступ; harness сам ничего не скачивает.

Сначала задайте root env variables новыми случайными значениями, не
используемыми нигде вне этого контейнера. Отдельно задайте два различных test
secret values в **MINIO_PLUGIN_ACCEPTANCE_ADMIN_SECRET_KEY** и
**MINIO_PLUGIN_ACCEPTANCE_PRESIGN_SECRET_KEY**. Затем в отдельном shell:

~~~bash
docker run --rm --name minio-plugin-acceptance \
  -p 127.0.0.1:9000:9000 \
  --env MINIO_ROOT_USER="$MINIO_PLUGIN_ACCEPTANCE_ROOT_ACCESS_KEY" \
  --env MINIO_ROOT_PASSWORD="$MINIO_PLUGIN_ACCEPTANCE_ROOT_SECRET_KEY" \
  ghcr.io/ne-tort/minio:RELEASE.2024-10-02T17-50-41Z \
  server /data --address :9000 --console-address :9001
~~~

Volume не подключается: удаление контейнера удаляет его disposable данные.
Loopback publishing не делает endpoint общим. Не включайте shell xtrace, чтобы
раскрытые env не попали в транскрипт. После готовности сервера задайте:

~~~bash
export MINIO_PLUGIN_ACCEPTANCE_ENDPOINT=http://127.0.0.1:9000
export MINIO_PLUGIN_ACCEPTANCE_CONFIRM_ISOLATED_TEST_ENV=I_OWN_DISPOSABLE_MINIO_RESOURCES
export MINIO_PLUGIN_ACCEPTANCE_MC_BINARY=/absolute/path/to/project/mc
export MINIO_PLUGIN_ACCEPTANCE_REQUIRED=1
~~~

## Будущий запуск

Из **apps/api**, после явного решения снять verification freeze:

~~~bash
pytest --confcutdir=tests/integration/minio_plugin_acceptance tests/integration/minio_plugin_acceptance -q
~~~

**--confcutdir=tests/integration/minio_plugin_acceptance обязателен.** Он
сохраняет локальные fixtures/no-skip gate, но не загружает родительский
apps/api/tests/conftest.py. Последний при import запускает PostgreSQL, live API и
kubectl probes, включая default live http://127.0.0.1:8088: это посторонние
side effects вне isolated MinIO. Не отключайте conftest целиком — это удалило бы
локальные fixtures и gate. При acceptance env локальный configure отклоняет
неправильный confcutdir через pytest.UsageError, а
**test_collection_isolation.py** статически проверяет команду README и отсутствие
parent conftest среди загруженных модулей.

Не исключайте **slow**: TTL=60 expiry — обязательная часть acceptance. При заданном
**MINIO_PLUGIN_ACCEPTANCE_ENDPOINT** локальные pytest hooks делают прогон ошибочным
при **collected=0** или любом **skipped>0**. Сам маркер зарегистрирован локально как
**minio_acceptance**; дополнительные application/dev/prod flags не нужны.

## Проверяемый контракт

1. Реальный AssumeRole; до probe операция typed-unwired. После probe новая scoped
   session даёт HeadBucket=200 и fixed HEAD=200 с ContentLength=0. Собственный
   prefix list разрешён; существующие foreign key/bucket дают ровно 403 и
   AccessDenied.
2. **objects.presign** подписывает exact-key GET/PUT/HEAD с TTL 60..3600, отдельным
   presigner principal и ровно шестью SigV4 query fields. Каждый положительный
   случай выполняет реальную HTTP операцию; PUT дополнительно подтверждается HEAD.
3. Cross-tenant, cross-plugin, wildcard, list, raw/encoded traversal и неподдерживаемые
   методы отклоняются. Изменение path/query/method уже подписанного URL, admin
   signing и endpoint substitution не принимаются.
4. TTL 59/3601 отклоняется. Отдельный **slow** тест ждёт фактического истечения
   TTL=60 и требует последующий 403 AccessDenied, не принимая 404 как expiry.
5. Реальные NoSuchBucket/NoSuchKey и намеренно лишённый статуса ответ реального
   HeadBucket не создают availability evidence. **objects.scoped_credentials**
   остаётся typed-unwired, не сохраняет credentials и не деградирует в presign.
   Legacy **io_test_sts_available**/**minio_sts_available** не включают операцию.
6. Точный confcutdir исключает parent import-time probes. Mc child env ограничен
   allowlist, HTTPX/boto3 игнорируют sentinel proxies, ambient secrets и signed
   URL не попадают в env subprocess или captured logs.

Успешность этих пунктов будет известна только после отдельного разрешённого
прогона. Сейчас несовместимость конкретного MinIO/mc release, STS session policy
или окружения остаётся явным непроверенным риском, а не скрытым pass/skip.
