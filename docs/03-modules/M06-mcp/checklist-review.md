# M06 — Чеклист ревью

| # | вопрос | балл | red flags |
| --- | --- | --- | --- |
| R1 | Полный Commerce mapping в mcp-tools.md? | | Missing pipeline.* |
| R2 | Profile filter до Agent.create? | | Filter only in prompt |
| R3 | include_on_order не существует? | | Legacy param |
| R4 | Community MCP disabled by default? | | Auto-enable |
| R5 | Uninstall только when disabled? | | Delete enabled proc |
| S1 | Secrets not in session_bindings? | | JSON env dump |
| S2 | Custom bundle scan? | | Upload arbitrary py |
| S3 | Process runs as non-root? | | Same user as API |
| S4 | admin-debug role-gated? | | Available to operator |
| S5 | Audit on enable/disable? | | Silent |
| A1 | Discovery timeout handled? | | Hang forever |
| A2 | effective-tools preview accurate? | | Stale cache |
| A3 | Plan limit on install? | | Unlimited |
| A4 | Idempotent enable? | | Duplicate processes |
| A5 | OpenAPI internal session-bind? | | Undocumented |
| T1 | Manifest diff Commerce vs Prodavan CI? | | Manual |
| T2 | kp denylist regression? | | cache_purge exposed |
| T3 | Cross-cabinet installation isolation? | | Shared installation_id |
| T4 | Rediscover updates catalog? | | Stale tools |
| T5 | max_tools edge case? | | Silent truncate |
| O1 | mcp_tool_calls_total labeled? | | Missing labels |
| O2 | stderr logs rotated? | | Disk fill |
| O3 | Zombie process reaper? | | Pids accumulate |
| O4 | Runbook linked? | | |
| O5 | Version pin enforced? | | Floating latest |

**Вердict:** Approve если среднее ≥ 8, mapping parity verified, нет red flags.
