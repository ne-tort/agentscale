# Kafka/Redpanda plugin-event real-service acceptance

> **НЕ ЗАПУЩЕНО.** В рамках этой правки тесты, collection, import/compile checks,
> build, lint, docker, kubectl и live/dev/prod/shared-environment calls не
> выполнялись. Это новый harness для отдельного разрешённого запуска после
> verification freeze, а не evidence пройденного acceptance.

Каталог изолирован от общих tests conftest: fixtures находятся только в
локальном [conftest.py](conftest.py), а topic/group/admin lifecycle вынесен в
[resources.py](resources.py). Production code не изменён.

## Что реально используется

- Kafka или Redpanda только по явному **KAFKA_PLUGIN_ACCEPTANCE_BOOTSTRAP_SERVERS**;
  никакого default endpoint.
- Реальные **AIOKafkaProducer**, **AIOKafkaConsumer**, **AIOKafkaAdminClient** и
  production **AiokafkaProducerAdapter**, **AiokafkaConsumerAdapter**,
  **KafkaTopicAdminAdapter**, **KafkaDlqAdapter**, **KafkaRawDlqAdapter**,
  **PluginEventDispatcher**, **HmacPluginEventSigner**,
  **ActivatingPluginEventHandler**.
- Реальные PostgreSQL repositories для subscription, producer/consumer
  idempotency, grant и append-only audit. Каждый тест создаёт случайную SQL
  schema и удаляет только её.
- Реальный Redis для production raw-DLQ natural-key dedup и обязательных
  **RedisDeliveryClaimStore** leases. Один и тот же isolated Redis DSN
  используется для обоих production adapters; отдельный env не требуется.
  Удаляются только точные owned keys этого теста; **FLUSHDB** не используется.
- Реальный localhost HTTP backend через production **HttpProxyPort** и
  **BackendHttpAdapter**. Backend проверяет JWT и HMAC event signature и пишет
  только id/digest observations.

Mock Kafka clients, in-memory event stores, fake DLQ и поддельные proxy responses
не используются. Единственная service-scope test fixture —
**TestOnlyWakeFixture**: она использует реальный SQL grant и production JWT mint,
но **не выполняет Kubernetes scale/Ready/TokenRequest**. Поэтому этот harness
не является live K8s wake acceptance и не снимает STOP-SHIP или production
configuration gates.

## Acceptance contract

| Сценарий | Обязательный результат |
|---|---|
| valid HMAC sign/verify | acquire перед wake, renew перед handler, backend проверяет JWT/HMAC, complete только после 2xx, затем manual commit |
| unsigned / tampered / malformed | raw DLQ с digest/source coordinates, затем commit; ноль wake и ноль handler/backend calls |
| duplicate ACK, включая restart consumer | durable SQL dedup + Redis completed claim; новая generation без повторного wake/backend, lease release после finalization |
| unknown subscription / revoked subscription | UNKNOWN_SUBSCRIPTION до poll/commit |
| revoked grant | три fail-closed wake-binding попытки, valid-event DLQ, ноль wake/backend; commit только после DLQ |
| unknown topic | INVALID_EVENT, seek на тот же offset, без DLQ и без handler |
| retry/seek | один claim на все попытки, два RETRY со seek, complete только на успешной; permanent failure даёт DLQ до commit и release |
| independent event key | event HMAC и claims JWT используют разные refs и разные resolved keys; нет fallback на Kafka credentials |
| rebalance после delivery до failed commit | completed Redis claim + SQL idempotency позволяют новому owner ACK без повторного backend |
| missing DeliveryClaimPort | early MISSING_DEPENDENCY до poll, без seek/commit, ноль wake/backend и ноль claim keys |
| assignment loss после WakeResult и до handler | обязательный typed DeliveryClaimError; backend/commit/DLQ = 0; затем generation2, ровно один backend и commit нового owner |

Все операции имеют bounded deadlines. Consumer config остаётся production:
**earliest**, **enable_auto_commit=False**, **dispatch**, **MAX_DELIVERY_ATTEMPTS=3**.
Delivery lease TTL вычисляется из существующего operation timeout и ограничен
30 секундами; отдельная lease env не вводится.

### Mandatory rebalance acceptance, без xfail

**test_rebalance_after_wake_before_handler_blocks_stale_delivery_then_recovers**
использует реальных двух consumers и gate после построения настоящего
**WakeResult**, но до возврата в dispatcher. После потери assignment обязателен
typed **DeliveryClaimError** с retryable=true и side_effects_uncertain=false;
backend, valid/raw DLQ и commit запрещены. Затем owner-only release
заброшенного lease позволяет второму consumer получить generation2: ровно один
backend, complete и commit. Stale side effect, KafkaAdapterError вместо typed
ownership error или failed recovery — обычный failure, не xfail.

**KAFKA_PLUGIN_ACCEPTANCE_REQUIRE_COMPLETE=1** проходит только когда этот и все
остальные mandatory сценарии действительно зелёные. Отсутствие stale side
effect не используется как повод для unconditional failure или pending.

## Env

Все значения задаются только в isolated test process/CI environment. Не
используйте production/shared DSN, production settings или .env из репозитория.
В примерах нет реальных секретов. Не вставляйте значения в README, tickets,
pytest logs или shell history; передавайте их через secret manager раннера.

| Переменная | Назначение |
|---|---|
| KAFKA_PLUGIN_ACCEPTANCE_BOOTSTRAP_SERVERS | Explicit test Kafka/Redpanda bootstrap list; без него локально допустим module skip |
| KAFKA_PLUGIN_ACCEPTANCE_DATABASE_DSN | Только postgresql+asyncpg DSN isolated test DB с credentials; нужны CREATE SCHEMA/DROP SCHEMA |
| KAFKA_PLUGIN_ACCEPTANCE_REDIS_DSN | redis/rediss DSN isolated test Redis |
| KAFKA_PLUGIN_ACCEPTANCE_SECRET_ROOT | Внешняя read-only для harness директория test-only SecretStore |
| KAFKA_PLUGIN_ACCEPTANCE_EVENT_SIGNING_SECRET_REF | Например file://plugin-events/hmac-key; resolved key минимум 32 байта |
| KAFKA_PLUGIN_ACCEPTANCE_CLAIMS_SIGNING_SECRET_REF | Другой ref, например file://plugin-claims/hs256-key; resolved key минимум 32 байта |
| KAFKA_PLUGIN_ACCEPTANCE_CONFIRM_ISOLATED_TEST_ENV | Обязательно ровно I_OWN_ISOLATED_TEST_RESOURCES |
| KAFKA_PLUGIN_ACCEPTANCE_SECURITY_PROTOCOL | SASL_SSL по умолчанию; PLAINTEXT только для isolated local test |
| KAFKA_PLUGIN_ACCEPTANCE_SASL_USERNAME_REF | Для SASL_SSL: SecretStore ref, не plaintext username |
| KAFKA_PLUGIN_ACCEPTANCE_SASL_PASSWORD_REF | Для SASL_SSL: SecretStore ref, не plaintext password |
| KAFKA_PLUGIN_ACCEPTANCE_TLS_CA_FILE | Для SASL_SSL: externally provisioned CA path; production TLS verification включена |
| KAFKA_PLUGIN_ACCEPTANCE_SASL_MECHANISM | SCRAM-SHA-512 по умолчанию или SCRAM-SHA-256 |
| KAFKA_PLUGIN_ACCEPTANCE_ALLOW_LOCAL_PLAINTEXT | Для local PLAINTEXT обязательно ровно LOCAL_TEST_ONLY_NO_SHARED_ENVIRONMENT |
| KAFKA_PLUGIN_ACCEPTANCE_OPERATION_TIMEOUT_SECONDS | 20 по умолчанию, допустимо 2..60 |
| KAFKA_PLUGIN_ACCEPTANCE_REBALANCE_TIMEOUT_SECONDS | 30 по умолчанию, допустимо 5..90 |
| KAFKA_PLUGIN_ACCEPTANCE_REQUIRED | 1 включает contract collected>0, skipped=0 |
| KAFKA_PLUGIN_ACCEPTANCE_REQUIRE_COMPLETE | 1 дополнительно запрещает explicit pending/xfail |

Для SASL_SSL используйте **rediss://** для Redis. Local PLAINTEXT допускает
**redis://**. Не задавайте SASL/TLS inputs при **PLAINTEXT**.

Ожидаемый layout SecretStore (имена — примеры, содержимое сюда не копировать):

~~~text
<KAFKA_PLUGIN_ACCEPTANCE_SECRET_ROOT>/
  kafka/sasl-username
  kafka/sasl-password
  plugin-events/hmac-key
  plugin-claims/hs256-key
~~~

Файлы signing keys содержат независимые случайные значения длиной минимум 32
байта. SASL credentials только non-empty. Secret refs должны различаться между
собой и с Kafka credential refs. **TestOnlyFileSecretStore** запрещает absolute
paths, .. и выход за root, в том числе через resolved path.

## Точные команды

Запускать из **apps/api** в отдельном процессе с уже установленными project dev
зависимостями и provisioned isolated services. Ниже — команды для **будущего
разрешённого запуска**, они не выполнялись при создании harness.

### Обязательная isolation от parent conftest

Parent **apps/api/tests/conftest.py** при import вычисляет
**requires_postgres**, **requires_live_api**, **requires_k8s** и выполняет
PostgreSQL TCP, live API HTTP и kubectl probes. Поэтому единственная
поддерживаемая форма запуска включает **оба** аргумента:

~~~text
-p tests.integration.kafka_plugin_acceptance.acceptance_plugin
--confcutdir=tests/integration/kafka_plugin_acceptance
~~~

**--noconftest запрещён**: локальные fixtures, signing/env gates и session
contract должны остаться активными. Опция **-p** тоже обязательна: early hook
**pytest_load_initial_conftests** отклоняет missing/wrong confcutdir и
--noconftest **до загрузки parent conftest**. Bare/direct pytest invocation не
поддерживается. Повторная проверка в local conftest и статические сценарии
[test_invocation_isolation.py](test_invocation_isolation.py) фиксируют активный
cutoff, ранний guard и наличие локальных gates.

Промежуточного **tests/integration/conftest.py** нет; это подтверждено glob и
static guard. Sibling acceptance conftest не являются промежуточными и не
загружаются при указанном cutoff. Static guard-тесты не требуют service
fixtures, но в этой правке также не запускались.

### SASL_SSL acceptance

~~~bash
export KAFKA_PLUGIN_ACCEPTANCE_BOOTSTRAP_SERVERS='<test-bootstrap-host>:9093'
export KAFKA_PLUGIN_ACCEPTANCE_DATABASE_DSN='postgresql+asyncpg://<test-user>:<test-password>@<test-db-host>:5432/<test-db>'
export KAFKA_PLUGIN_ACCEPTANCE_REDIS_DSN='rediss://<test-user>:<test-password>@<test-redis-host>:6380/0'
export KAFKA_PLUGIN_ACCEPTANCE_SECRET_ROOT='<external-test-secret-root>'
export KAFKA_PLUGIN_ACCEPTANCE_EVENT_SIGNING_SECRET_REF='file://plugin-events/hmac-key'
export KAFKA_PLUGIN_ACCEPTANCE_CLAIMS_SIGNING_SECRET_REF='file://plugin-claims/hs256-key'
export KAFKA_PLUGIN_ACCEPTANCE_SASL_USERNAME_REF='file://kafka/sasl-username'
export KAFKA_PLUGIN_ACCEPTANCE_SASL_PASSWORD_REF='file://kafka/sasl-password'
export KAFKA_PLUGIN_ACCEPTANCE_TLS_CA_FILE='<external-test-ca.pem>'
export KAFKA_PLUGIN_ACCEPTANCE_SASL_MECHANISM='SCRAM-SHA-512'
export KAFKA_PLUGIN_ACCEPTANCE_SECURITY_PROTOCOL='SASL_SSL'
export KAFKA_PLUGIN_ACCEPTANCE_CONFIRM_ISOLATED_TEST_ENV='I_OWN_ISOLATED_TEST_RESOURCES'
export KAFKA_PLUGIN_ACCEPTANCE_REQUIRED=1
python -m pytest \
  -p tests.integration.kafka_plugin_acceptance.acceptance_plugin \
  --confcutdir=tests/integration/kafka_plugin_acceptance \
  tests/integration/kafka_plugin_acceptance -q
~~~

Contract требует **collected > 0** и **skipped = 0**; plugin сам меняет успешный
exit status на failure при нарушении. Rebalance xfail/pending больше не
используется. Полный режим, который дополнительно отклонит любой новый xfail:

~~~bash
KAFKA_PLUGIN_ACCEPTANCE_REQUIRE_COMPLETE=1 \
KAFKA_PLUGIN_ACCEPTANCE_REQUIRED=1 \
python -m pytest \
  -p tests.integration.kafka_plugin_acceptance.acceptance_plugin \
  --confcutdir=tests/integration/kafka_plugin_acceptance \
  tests/integration/kafka_plugin_acceptance -q
~~~

Команда проходит только при typed ownership error и успешном generation2
recovery. Маскировка ошибки seek/ownership, stale backend side effect или
невозможность recovery является обычным failure, а не xfail/pending.

### Local PLAINTEXT, только isolated services

~~~bash
export KAFKA_PLUGIN_ACCEPTANCE_SECURITY_PROTOCOL='PLAINTEXT'
export KAFKA_PLUGIN_ACCEPTANCE_ALLOW_LOCAL_PLAINTEXT='LOCAL_TEST_ONLY_NO_SHARED_ENVIRONMENT'
unset KAFKA_PLUGIN_ACCEPTANCE_SASL_USERNAME_REF
unset KAFKA_PLUGIN_ACCEPTANCE_SASL_PASSWORD_REF
unset KAFKA_PLUGIN_ACCEPTANCE_TLS_CA_FILE
export KAFKA_PLUGIN_ACCEPTANCE_REDIS_DSN='redis://<test-user>:<test-password>@<local-test-redis>:6379/0'
# Остальные required env и pytest command — как в SASL_SSL примере.
~~~

### Локальный запуск без endpoint

Если **KAFKA_PLUGIN_ACCEPTANCE_BOOTSTRAP_SERVERS** отсутствует, разрешён module
skip. Но при **KAFKA_PLUGIN_ACCEPTANCE_REQUIRED=1** этот skip является failure.

~~~bash
unset KAFKA_PLUGIN_ACCEPTANCE_BOOTSTRAP_SERVERS
unset KAFKA_PLUGIN_ACCEPTANCE_REQUIRED
python -m pytest \
  -p tests.integration.kafka_plugin_acceptance.acceptance_plugin \
  --confcutdir=tests/integration/kafka_plugin_acceptance \
  tests/integration/kafka_plugin_acceptance -q
~~~

## Resource ownership и cleanup

Каждый тест получает уникальные:

- основной topic, **topic.dlq**, unknown topic;
- subscription id и consumer group; отдельную observer group при чтении DLQ;
- SQL schema;
- raw-DLQ Redis natural keys;
- per-test RedisDeliveryClaimStore prefix и точные lease/completed/generation
  triple keys для каждого (subscription_id, source_event_id).

Topics создаются через production admin adapter (RF=1, одна partition).
Consumers останавливаются до **delete_consumer_groups**; группы удаляются до
topic deletion. Admin deletion дожидается исчезновения topics в bounded loop.
**UnknownTopicOrPartition**/**GroupIdNotFound** допустимы только как
идемпотентный повтор cleanup. Другие ошибки cleanup приводят к failure.

Для random-topic raw DLQ используется **KafkaRawDlqAdapter** с уже запущенным
реальным producer. **KafkaEventResourceManager.start()** не вызывается, потому
что он создаёт фиксированный production topic. Это composition detail, а не
эмуляция DLQ и не evidence random-topic support в production manager.

**TrackedDeliveryClaims** только делегирует production
**RedisDeliveryClaimStore.acquire/renew/complete/release** и запоминает точные
triple keys. После остановки consume tasks/consumers cleanup удаляет все
owned lease/completed/generation keys и подтверждает их отсутствие через GET.
Не используются SCAN, FLUSHDB, удаление по общему prefix или чужие keys.
Per-test prefix дополнительно исключает пересечение параллельных runs.

## Безопасность логов

- Plaintext event/claims keys, delivery claim owner tokens, DSN, SASL values,
  JWT, claims и raw body canary не печатаются.
- Backend отключает HTTP request logging и хранит только ids/digests.
- **assert_safe_dlq** запрещает plaintext **payload**, **body**, **claims**,
  **token**, **secret**, **authorization**, **event_signature** и проверяет
  sensitive canaries.
- Fixture после теста проверяет captured logs на отсутствие значений из
  SecretStore/DSN/JWT/canary; failure message не выводит найденный секрет.
- Signed event по-прежнему содержит только **payload_digest**, а не исходный
  payload. HMAC signature не является plaintext key.

## Намеренно невыполненные проверки

При этой правке **не запускались**:

- сам harness, любые backend/frontend tests и pytest collection;
- build, lint, formatting, compile/import smoke;
- docker, kubectl, helm, kustomize, terraform;
- git status/diff/log и другие git checks;
- любые подключения к live/dev/prod/shared Kafka, PostgreSQL, Redis, K8s или
  deployed entrypoints.

Commit/push/PR не выполнялись по прямому ограничению задачи. Файлы — новый
acceptance harness, не заявление о его прохождении.
