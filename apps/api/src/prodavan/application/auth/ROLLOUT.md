# Auth command/event bus: security rollout

Этот срез устраняет доверие к самому факту записи в `prodavan.auth.commands`, а
также в `prodavan.auth.events` до identity bind, и убирает пароли из новых
command messages. Он **не включает SASL/TLS/ACL Kafka** и не заменяет
NetworkPolicy: untrusted pods нельзя возвращать к broker до отдельного
infra-изменения с аутентификацией и минимальными ACL.

## Обязательная конфигурация

1. Сгенерировать независимый ключ минимум из 32 случайных байт (рекомендуется
   48 байт, например `secrets.token_urlsafe(48)`). Не использовать
   `AUTH_TEST_SECRET`, plugin/agent secrets или зашитый dev default.
2. Положить его в platform control-plane Secret как
   `AUTH_COMMAND_SIGNING_SECRET` и выдать **только** API/auth producer и auth
   consumer replicas. Не монтировать в plugin/agent pods, их env или volumes.
3. Настроить общий Redis >= 6.2 (atomic `GETDEL`) для всех producer/consumer
   replicas. Локальная файловая SecretStore и process-local fallback не участвуют
   в lifecycle. Redis network/ACL тоже ограничивается control plane: plugin/agent
   не должен читать или удалять auth credential keys, иначе bearer ref можно
   перехватить вне consumer.
   `AUTH_REGISTRATION_PASSWORD_TTL_SECONDS=60` (допустимо 5..300).
4. Сгенерировать отдельный `AUTH_EVENT_SIGNING_SECRET` (тоже минимум 32
   случайных байт) для identity-bind events. Он не должен совпадать с command
   secret и тоже не имеет dev fallback. Установить
   `AUTH_EVENT_MAX_AGE_SECONDS=300`; replay markers TTL = max age + 60.
5. Оставить bounded retry: `AUTH_REGISTRATION_RETRY_ATTEMPTS=3`,
   `AUTH_REGISTRATION_RETRY_DELAY_SECONDS=0.2`. Consumer без валидных обоих
   ключей не стартует; producer без нужного ключа не публикует command/event.

## Порядок cutover ( maintenance window )

1. Приостановить новые registration/password-reset операции и auth event
   fanout. Не допускается rolling-смесь старых и новых auth producers/consumers:
   старые producers unsigned (commands имеют plaintext, events — broker trust),
   а старые consumers не понимают `password_ref` и подписанные events.
2. Остановить старые auth consumers, затем старые producers. Retained legacy
   messages считать скомпрометированными: commands могут содержать plaintext
   passwords, а retained unsigned events не должны достигать нового bind path.
3. Развернуть новые consumers с обоими dedicated secret и общим Redis, затем
   новые producers. DLQ topics по умолчанию: `prodavan.auth.commands.dlq` и
   `prodavan.auth.events.dlq` (или `.dlq` рядом с кастомным topic).
4. Retained unsigned/invalid commands/events не исполняются и не биндятся:
   consumer отправляет в соответствующий DLQ только sanitized metadata
   (topic/partition/offset/event id hash/reason), увеличивает
   `auth_commands_rejected`/`auth_commands_dlq` и
   `auth_events_rejected`/`auth_events_dlq`, пишет log без payload.
5. После cutover удалить старые plaintext commands и старые unsigned events из
   retained topics по утверждённой Kafka retention/delete/recreate процедуре и
   сбросить consumer groups на чистые topics. DLQ не является хранилищем для
   replay исходных сообщений. Все потенциально раскрытые пароли нужно
   ротацировать через новый internal registration/password-reset path.
6. Возобновить операции только после проверки, что все replicas имеют новый
   образ/secret, а legacy plaintext больше не доступен untrusted workloads.

Rollback тоже атомарный для producer+consumer: сначала остановить producers,
дождаться обработки signed commands, затем остановить consumers. Откат на старый
consumer при живых новых producers запрещён. Refs истекают по TTL и не должны
использоваться как долговременный outbox.

## Transient Keycloak failure и повторная выдача

Consumer делает Redis `GETDEL` ровно один раз для ref. При transient Keycloak
ошибке он выполняет bounded synchronous retry с typed password только в памяти;
create-or-reuse + password/role reconciliation сохраняет идемпотентность, если
Keycloak успел создать user перед потерей ответа. Старый ref не восстанавливается.
После исчерпания бюджета публикуется `auth.user.register_failed` с
`retryable=true`. Контролируемый последующий retry — повторный вызов internal
`publish_register_command` с typed password (например, повторить исходный
password-reset request): он создаёт новый high-entropy ref с новым TTL и новой
подписью. Replay старого сообщения или старого ref запрещён. Межпроцессный
durable retry после crash не заявляется; это availability trade-off, а не путь
к повторному использованию секрета.

## Auth events / identity bind

Producer edge `KafkaManager.publish` подписывает **все** `auth_event` envelopes
отдельным `AUTH_EVENT_SIGNING_SECRET`; прямой domain `register_user` остаётся
unsigned до bus edge. Consumer проверяет HMAC, schema, timezone-aware freshness
(`AUTH_EVENT_MAX_AGE_SECONDS`) и делает Redis `SET NX EX` claim `event_id` до
`_apply_auth_bind`. Повтор, expired/future/tampered/unsigned event не вызывает
bind: он попадает в sanitized `auth.events.dlq` и counters/log. Replay marker
хранит только SHA-256 hash id. Claim делается до bind и намеренно не откатывается
при crash/error, чтобы не допустить duplicate bind; availability retry в этом
случае — новый trusted result с новым `event_id` (повторный idempotent
registration flow), не replay старого события. Отдельный auth-event claim marker
не является password credential и недоступен untrusted workloads при Redis ACL.

## Остаточная изоляция

HMAC доказывает происхождение, но не шифрует topic и не защищает от DoS/чтения
metadata. Password ref без доступа к общему Redis бесполезен, а после GETDEL
бесполезен и для повторной доставки. Passwordless lifecycle commands остаются
чувствительными к durable command replay (event replay закрыт, command replay
кроме consumed password refs не добавлен); их отдельная anti-replay защита и
broker ACL — infra/дальнейший срез. NetworkPolicy нельзя убирать до SASL/TLS/ACL.
