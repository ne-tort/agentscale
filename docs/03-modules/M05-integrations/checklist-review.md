# M05 — Чеклист ревью

Независимое ревью перед merge/release. Балл **1–10** на пункт.

## Архитектура

| # | вопрос | балл | red flags |
| --- | --- | --- | --- |
| R1 | Нет глобальных allowlist/trusted lists? | | Shared table without cabinet_id |
| R2 | Rate limit до HTTP egress? | | Limit only in UI |
| R3 | on_order исключён на всех слоях? | | Flag in agent prompt only |
| R4 | Чёткая граница M05 vs M04 catalog? | | Duplicate search logic |
| R5 | Internal API не exposed publicly? | | /internal без mTLS/network policy |

## Безопасность

| # | вопрос | балл | red flags |
| --- | --- | --- | --- |
| S1 | Credentials encrypted, never logged? | | Plaintext in env file committed |
| S2 | JWT cabinet_id matches resource? | | Trust client header X-Cabinet |
| S3 | Web search только allowlist shop_id? | | Arbitrary URL parameter |
| S4 | RBAC matches security.md matrix? | | Operator can rotate creds |
| S5 | Audit on policy/creds changes? | | Silent credential update |

## Качество API

| # | вопрос | балл | red flags |
| --- | --- | --- | --- |
| A1 | OpenAPI актуален? | | Drift from code |
| A2 | Error codes documented? | | Generic 500 only |
| A3 | Pagination on trusted sellers (large)? | | Unbounded list |
| A4 | Idempotent PUT credentials? | | Duplicate rows |
| A5 | 429 with Retry-After? | | Empty body 429 |

## MCP / агент

| # | вопрос | балл | red flags |
| --- | --- | --- | --- |
| M1 | Tool results без secrets/HTML? | | Raw API dump |
| M2 | Commerce parity documented? | | Missing s4b.cache_* |
| M3 | Profile filter excludes dangerous tools? | | cache_purge in readonly |
| M4 | Cabinet context from session not args? | | cabinet_id tool arg |
| M5 | Rate limit surfaced to agent? | | Opaque timeout |

## Тесты

| # | вопрос | балл | red flags |
| --- | --- | --- | --- |
| T1 | Cross-cabinet isolation test? | | None |
| T2 | Rate limit integration test? | | Manual only |
| T3 | Category filter fixtures? | | No non-electronics case |
| T4 | E2E S4B test (mock)? | | Live API only |
| T5 | Regression on_order? | | Missing |

## UI/UX

| # | вопрос | балл | red flags |
| --- | --- | --- | --- |
| U1 | Confirm on disable S4B? | | Instant toggle |
| U2 | Credentials never shown after save? | | Password in GET |
| U3 | Empty allowlist explained? | | Blank screen |
| U4 | Rate limit visible to admin? | | Hidden until 429 |
| U5 | a11y toggles? | | Color-only status |

## Наблюдаемость

| # | вопрос | балл | red flags |
| --- | --- | --- | --- |
| O1 | integration_call_log populated? | | Silent failures |
| O2 | Prometheus metrics (M09)? | | No counters |
| O3 | Alert on S4B auth failure spike? | | No alerting |
| O4 | Runbook linked in README? | | Missing |
| O5 | Quota exceeded user message? | | 507 generic |

## Вердикт ревью

| итог | условие |
| --- | --- |
| **Approve** | все ≥ 7, нет red flags, среднее ≥ 8 |
| **Approve with notes** | один пункт 5–6, план fix ≤ 1 sprint |
| **Reject** | любой red flag или пункт ≤ 4 |

**Ревьюер:** _______________  
**Дата:** _______________  
**Средний балл:** ___ / 10
