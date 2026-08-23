# Cron / ops hooks (L07/L09)

Opt-in in-process workers are **off by default**. Prefer external cron or admin UI.

## Idle pause (platform-wide)

```bash
# Requires platform.admin bearer token
curl -X POST "$API/api/v1/admin/triggers/idle-pause/sweep" \
  -H "Authorization: Bearer $TOKEN"
```

Company-scoped:

```bash
curl -X POST "$API/api/v1/admin/companies/$COMPANY_ID/idle-pause/sweep" \
  -H "Authorization: Bearer $TOKEN"
```

Policy: `PUT .../agent-policy` with `idle_pause_after_hours` > 0 (0/null = off).

Env alternative (API process): `IDLE_PAUSE_WORKER_ENABLED=true` shares the trigger worker loop.

## Trigger drain

```bash
curl -X POST "$API/api/v1/admin/triggers/drain" \
  -H "Authorization: Bearer $TOKEN"
```

Env: `TRIGGER_WORKER_ENABLED=true` (+ outbox lease settings in `.env.example`).
