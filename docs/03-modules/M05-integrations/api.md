# M05 — HTTP API

Базовый префикс: `/api/v1/cabinets/{cabinet_id}/integrations`

Аутентификация: JWT (M08). Требуемые роли см. [security.md](security.md).

## Политика кабинета

### GET `/policy`

Получить текущую политику интеграций.

**Response 200**

```json
{
  "cabinet_id": "550e8400-e29b-41d4-a716-446655440000",
  "s4b_enabled": true,
  "s4b_trusted_only": true,
  "s4b_electronics_only": true,
  "web_shops_enabled": true,
  "default_rate_limit_rpm": 60,
  "s4b_credentials_configured": true
}
```

` s4b_credentials_configured` — boolean, без раскрытия секретов.

### PATCH `/policy`

**Body** (partial):

```json
{
  "s4b_enabled": true,
  "s4b_trusted_only": false,
  "default_rate_limit_rpm": 120
}
```

**Response 200** — обновлённая политика.

## S4B credentials

### PUT `/s4b/credentials`

**Body:**

```json
{
  "login": "cabinet_login",
  "password": "secret"
}
```

Пароль шифруется at-rest (AES-256-GCM, ключ из KMS). В ответе пароль не возвращается.

### DELETE `/s4b/credentials`

Сброс учётных данных. `s4b_enabled` автоматически не меняется — вызовы S4B вернут `412`.

### POST `/s4b/test`

Проверка подключения (`s4b_ping` эквивалент).

**Response 200**

```json
{
  "ok": true,
  "latency_ms": 210,
  "api_version": "2.1"
}
```

## Trusted sellers S4B

### GET `/s4b/trusted-sellers`

**Query:** `?enabled=true&limit=100&offset=0`

**Response 200**

```json
{
  "items": [
    {
      "id": "uuid",
      "s4b_seller_id": 12345,
      "seller_name": "ООО Пример",
      "enabled": true
    }
  ],
  "total": 1
}
```

### POST `/s4b/trusted-sellers`

**Body:**

```json
{
  "s4b_seller_id": 12345,
  "seller_name": "ООО Пример"
}
```

### PATCH `/s4b/trusted-sellers/{id}`

**Body:** `{ "enabled": false }`

### DELETE `/s4b/trusted-sellers/{id}`

## Web shops allowlist

### GET `/web-shops`

Список записей allowlist + статус адаптера.

**Response 200**

```json
{
  "items": [
    {
      "id": "uuid",
      "shop_id": "dns",
      "display_name": "DNS",
      "enabled": true,
      "priority": 10,
      "rate_limit_rpm": 30,
      "adapter_status": "healthy"
    }
  ]
}
```

### POST `/web-shops`

**Body:**

```json
{
  "shop_id": "dns",
  "enabled": true,
  "priority": 10,
  "rate_limit_rpm": 30
}
```

### PATCH `/web-shops/{id}`

### DELETE `/web-shops/{id}`

## Rate limits (read-only для UI)

### GET `/rate-limits/status`

Текущие счётчики по интеграциям.

**Response 200**

```json
{
  "buckets": [
    {
      "integration": "s4b",
      "operation": "search",
      "limit": 60,
      "remaining": 45,
      "reset_at": "2026-08-20T10:01:00Z"
    }
  ]
}
```

## Internal API (service-to-service)

Префикс: `/internal/v1/integrations`

Вызывается MCP-адаптерами (M06), не публично.

### POST `/check-rate-limit`

**Body:**

```json
{
  "cabinet_id": "uuid",
  "integration": "s4b",
  "operation": "search_articles"
}
```

**Response 200:** `{ "allowed": true, "remaining": 59 }`  
**Response 429:** `{ "allowed": false, "retry_after_seconds": 42 }`

### GET `/effective-policy/{cabinet_id}`

Агрегированная политика для runtime (кэш 60s).

## OpenAPI

Полная спецификация: `openapi/m05-integrations.yaml` (генерируется из кодогенерации).

## Версионирование

- Breaking changes → `/api/v2/...`
- Поля `shop_id`, коды ошибок — стабильный контракт
