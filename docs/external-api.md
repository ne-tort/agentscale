# External Chat API + Webhooks

Машинный доступ к чатам агента извне (для интеграций) + исходящие вебхуки
(ChatGPT-style). PR: external chat API.

## Аутентификация: app keys

Ключ приложения (`ak_...`) создаёт сотрудник в настройках кабинета и действует
**от имени** этого сотрудника (участника кабинета). Секрет показывается один
раз при создании; в БД только sha256-хэш. Скоупы: `chat:read`, `chat:write`,
`catalog:read`. Поля: `label`, `company_id`, `cabinet_id` (опциональный скоуп),
`employee_id` (auth-as), `token_prefix`, `secret_hash`, `scopes`, `status`,
`expires_at`, `last_used_at`.

Управление (под OIDC-сотрудником): `POST /api/v1/external/app-keys`,
`GET /api/v1/external/app-keys`, `DELETE /api/v1/external/app-keys/{id}`.

## Внешний чат-API (`/api/v1/external`, Bearer ak_…)

| Метод | Путь | Что делает |
|---|---|---|
| GET | `/external/cabinets` | кабинеты сотрудника (скоуп ключа) |
| GET | `/external/cabinets/{cid}/projects` | проекты кабинета |
| GET | `/external/projects/{pid}/chats` | чаты проекта |
| POST | `/external/projects/{pid}/chats` | создать чат (title/model) |
| GET | `/external/chats/{sid}` | статус чата |
| POST | `/external/chats/{sid}/messages` | отправить сообщение (`wait=true` — ждать ответ; иначе detached turn) |
| GET | `/external/chats/{sid}/messages?after_seq=` | инкрементальный транскрипт |
| GET | `/external/chats/{sid}/answer` | последний ответ агента + turn_in_progress |

Доступ к проекту/кабинету проверяется по участию сотрудника (cabinet
assignment) и company ключа.

## Исходящие вебхуки

Подписка: `POST /api/v1/external/webhooks {url, events, project_id?}` →
возвращает `secret` один раз (HMAC живёт в secret store, не в БД).

События: `chat.message.completed` (ответ агента готов), `chat.turn.failed`.

Доставка (outbox `webhook_deliveries` + celery beat каждые 15с):

- подпись тела: `X-Prodavan-Signature: sha256=<hmac>`, заголовки
  `X-Prodavan-Event`, `X-Prodavan-Delivery`;
- ретраи с бэкоффом 1м/5м/30м/2ч (5 попыток), потом `exhausted`;
- подписка авто-отключается после 10 подряд ошибок;
- claim через `FOR UPDATE SKIP LOCKED` + lease (crash-safe).

Верификация на стороне получателя: `sha256 = HMAC(secret, raw_body)`.
