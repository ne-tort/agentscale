# Explicit production capability pending

Этот файл фиксирует открытые ограничения, которые нельзя считать passed
acceptance. Он не заменяет выполнение тестов и не меняет STOP-SHIP status.
Kafka rebalance/assignment recovery не входит в pending: это обязательный
сценарий с typed ownership error и generation2 recovery, написанный но не
запущенный в этой правке.

## 1. Настоящий K8s wake

**TestOnlyWakeFixture** проверяет dispatcher wake ordering, SQL grant и production
JWT mint, но не Kubernetes scale, readiness, TokenRequest или реальный
**PluginActivator**. Эти свойства остаются отдельным live acceptance.

## 2. Production manager random-topic composition

Acceptance использует реальный **KafkaRawDlqAdapter** с уже запущенным реальным
producer, чтобы не создавать фиксированный production topic через
**KafkaEventResourceManager.start()**. Поддержка arbitrary per-test topic в самом
production resource manager не проверяется и не заявляется.

## 3. Что этот harness не закрывает

- production SealedSecret resolution/mount, key rotation и enabled API/Gateway
  entrypoints;
- production promotion, deployment, real backend identity в K8s;
- exactly-once side effects для всех конкурентных окон (контракт здесь —
  at-least-once Kafka + durable dedup, а не распределённая exactly-once
  транзакция Kafka/PostgreSQL/HTTP);
- любые live/dev/prod/shared environment properties.

## 4. Subscription plugin_id fidelity

Текущий **DbSubscriptionStore** восстанавливает **plugin_id** из suffix
**consumer_group**, а не из отдельной durable колонки. Текущий dispatcher
сравнивает id/group/event_types, а **ActivatingPluginEventHandler** использует
исходную typed subscription, поэтому harness не подменяет store и не скрывает
это ограничение. Полный durable plugin_id round-trip отдельно остаётся
unverified production risk, а не заявленным acceptance pass.
