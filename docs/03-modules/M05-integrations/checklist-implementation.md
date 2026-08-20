# M05 — Чеклист реализации

Оценка **1–10** по каждому пункту: 1 = не начато, 10 = prod-ready с тестами и мониторингом.

## Схема и домен

| # | пункт | балл | критерий 10 |
| --- | --- | --- | --- |
| 1.1 | DDL миграции PostgreSQL | | Все таблицы, RLS, FK, rollback |
| 1.2 | Domain models + validation | | Pydantic/Zod, все error codes |
| 1.3 | Effective policy aggregator | | Unit tests: override hierarchy |
| 1.4 | S4B electronics category filter | | Fixture: mixed categories → correct filter |
| 1.5 | in_stock-only invariant | | Regression: on_order never in Offer |

## API

| # | пункт | балл | критерий 10 |
| --- | --- | --- | --- |
| 2.1 | CRUD policy REST | | OpenAPI, integration tests |
| 2.2 | S4B credentials encrypt/decrypt | | KMS mock + rotation test |
| 2.3 | Trusted sellers CRUD | | Duplicate seller_id → 409 |
| 2.4 | Web allowlist CRUD | | Unknown shop_id → 400 |
| 2.5 | Internal rate-limit API | | Load test 1k rps |

## Rate limiting

| # | пункт | балл | критерий 10 |
| --- | --- | --- | --- |
| 3.1 | Redis sliding window | | Accurate under concurrency |
| 3.2 | Per-shop override | | Integration test |
| 3.3 | 429 headers Retry-After | | Contract test |
| 3.4 | Fallback in-memory | | Documented limitation |
| 3.5 | Metrics export (M09) | | Prometheus counters wired |

## Адаптеры

| # | пункт | балл | критерий 10 |
| --- | --- | --- | --- |
| 4.1 | S4B adapter (search, ping) | | Sandbox credentials E2E |
| 4.2 | Web shop adapters (≥2) | | DNS + Ozon smoke |
| 4.3 | s4b-cache read/write/purge | | TTL + quota enforced |
| 4.4 | RawOffer normalization | | Schema validation |

## MCP

| # | пункт | балл | критерий 10 |
| --- | --- | --- | --- |
| 5.1 | prodavan-integrations server | | stdio + SSE |
| 5.2 | All s4b.* tools | | Parity with Commerce list |
| 5.3 | integrations.list_web_shops filter | | Empty allowlist → [] |
| 5.4 | Rate limit in tool path | | Tool returns retry hint |

## UI

| # | пункт | балл | критерий 10 |
| --- | --- | --- | --- |
| 6.1 | Overview screen | | Status cards live |
| 6.2 | S4B settings + trusted table | | E2E Playwright |
| 6.3 | Web shops allowlist UI | | Drag priority persists |
| 6.4 | Rate limits dashboard | | 24h chart from M09 |

## Безопасность и ops

| # | пункт | балл | критерий 10 |
| --- | --- | --- | --- |
| 7.1 | RLS policies active | | Cross-tenant test fails |
| 7.2 | RBAC middleware | | Matrix from security.md |
| 7.3 | Audit events emitted | | M09 ingestion verified |
| 7.4 | No secrets in logs | | Grep CI check |
| 7.5 | Runbook: S4B outage | | Documented + alert |

## Итого

| секция | сумма / max |
| --- | --- |
| Схема и домен | / 50 |
| API | / 50 |
| Rate limiting | / 50 |
| Адаптеры | / 40 |
| MCP | / 40 |
| UI | / 40 |
| Безопасность | / 50 |

**Целевой порог релиза:** средний балл ≥ 8, нет пунктов < 6.
