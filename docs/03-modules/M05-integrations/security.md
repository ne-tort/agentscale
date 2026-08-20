# M05 — Безопасность

## Модель угроз

| угроза | impact | митигация |
| --- | --- | --- |
| Утечка S4B credentials | доступ к чужим закупкам | encryption at rest, no logs |
| Cross-cabinet allowlist read | утечка политик | RLS + JWT cabinet_id |
| SSRF через web adapter | scan internal network | allowlist shop_id only, no custom URL |
| Rate limit bypass | DDoS внешних API | server-side limiter, не client |
| Подмена trusted seller | неверный primary offer | seller_id int from S4B API, audit log |

## RBAC (матрица M08)

| действие | platform.admin | tenant.admin | cabinet.admin | cabinet.operator | cabinet.viewer |
| --- | --- | --- | --- | --- | --- |
| GET policy | ✓ | ✓ | ✓ | ✓ | ✓ |
| PATCH policy | ✓ | ✓ | ✓ | — | — |
| PUT s4b credentials | ✓ | ✓ | ✓ | — | — |
| CRUD trusted sellers | ✓ | ✓ | ✓ | — | — |
| CRUD web allowlist | ✓ | ✓ | ✓ | — | — |
| POST s4b/test | ✓ | ✓ | ✓ | ✓ | — |

## Секреты

### S4B credentials

- Алгоритм: AES-256-GCM
- Ключ: tenant-scoped DEK из KMS (`tenant:{id}:integrations`)
- Rotation: manual + alert при `rotated_at > 90 days`
- **Запрещено:** логировать login/password, возвращать в API GET

### Env MCP worker

```text
CABINET_ID=uuid          # from session, not user-editable
S4B_LOGIN=...            # decrypted in memory only
S4B_PASSWORD=...
KMS_KEY_ID=...
```

## Сетевая изоляция

- Исходящие HTTP только к **известным hostnames** адаптеров:
  - `api.s4b.ru`
  - `www.dns-shop.ru`, `api.ozon.ru`, …
- Egress proxy deny-by-default
- mTLS optional для enterprise tenant

## Данные в промпте агента

**Не передавать:**

- S4B password
- Полные HTTP response bodies с PII
- Internal rate limit keys

**Можно:**

- Список enabled shop names
- Флаг trusted_only
- Aggregated error «rate limited»

## Аудит (M09)

События:

```json
{
  "event_type": "integration.policy_updated",
  "cabinet_id": "uuid",
  "actor_id": "uuid",
  "payload": { "s4b_enabled": true }
}
```

Credentials change:

```json
{
  "event_type": "integration.s4b_credentials_rotated",
  "payload": { "action": "put" }
}
```

Без значений секретов в payload.

## Compliance

- GDPR: call log retention 90 days default (M09)
- Нет глобального хранения прайсов между tenant — полная изоляция

## Чеклист pentest

- [ ] JWT другого cabinet_id → 403 на все endpoints
- [ ] shop_id произвольный URL → 400
- [ ] SQL injection в seller_name → parameterized
- [ ] Redis key injection → sanitized cabinet_id UUID
