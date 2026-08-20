# M06 — Чеклист реализации

| # | пункт | балл | критерий 10 |
| --- | --- | --- | --- |
| 1.1 | server_definitions seed (7 servers) | | Migration + manifest |
| 1.2 | Install/uninstall lifecycle | | State machine tests |
| 1.3 | Enable + discovery | | Mock MCP tools/list |
| 1.4 | Disable graceful shutdown | | No zombie processes |
| 1.5 | Error state + retry | | UI + API |
| 2.1 | Profile built-in kp | | Matches tools-catalog.md |
| 2.2 | tool_allowlist mode | | catalog-only E2E |
| 2.3 | tool_denylist mode | | cache_purge blocked |
| 2.4 | max_tools enforcement | | ToolBudgetExceeded |
| 2.5 | Custom profile CRUD | | Tenant scoped |
| 3.1 | session-bind internal API | | M07 integration |
| 3.2 | Env injection CABINET_ID | | Cross-cabinet fail |
| 3.3 | Commerce mapping parity | | Automated manifest diff |
| 3.4 | include_on_order absent | | Grep CI |
| 3.5 | list_web_shops → integrations | | Namespace test |
| 4.1 | tool_catalog persistence | | Rediscover upsert |
| 4.2 | Redis effective-tools cache | | Invalidation on enable |
| 4.3 | MCP supervisor process pool | | Health checks |
| 4.4 | SSE transport (optional) | | JWT auth |
| 5.1 | Registry UI install/enable | | Playwright |
| 5.2 | Profile preview effective-tools | | Live count |
| 5.3 | Tool catalog browser | | Search |
| 5.4 | Chat MCP chip (M07) | | Linked drawer |
| 6.1 | Community approval workflow | | Block until approved |
| 6.2 | Signed bundle verification | | Tamper test |
| 6.3 | Audit events M09 | | All lifecycle |
| 6.4 | Prometheus metrics | | discovery + tool_calls |
| 6.5 | Runbook discovery failure | | Linked |

**Целевой порог:** средний ≥ 8, mapping parity 10/10.
