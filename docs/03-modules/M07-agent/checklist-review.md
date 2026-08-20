# M07 — Чеклист ревью

| # | вопрос | балл | red flags |
| --- | --- | --- | --- |
| R1 | Chat isolated per project? | | Global inbox |
| R2 | reset re-binds MCP? | | Stale tools |
| R3 | cancel aborts provider? | | Zombie run |
| R4 | cwd = project root? | | Repo root |
| R5 | History survives reconnect? | | Lost deltas |
| S1 | Stream token or JWT secured? | | Open SSE URL |
| S2 | Tool output redacted? | | Raw env in stream |
| S3 | Attachment MIME enforced? | |任意 file |
| S4 | Cross-user session access blocked? | | IDOR |
| S5 | Message bodies excluded from audit? | | Full PII log |
| A1 | SSE Last-Event-ID works? | | Gap 410 only |
| A2 | WS/SSE event parity? | | Missing types |
| A3 | RUN_ALREADY_ACTIVE handled? | | Double run |
| A4 | 413 on large upload? | | OOM |
| A5 | Model allowlist enforced? | | Client-side only |
| T1 | E2E send message → tool → reply? | | Mock only |
| T2 | Reset regression MCP? | | |
| T3 | Extracted md for xlsx? | | Read binary |
| T4 | Token usage recorded? | | Zero always |
| T5 | Partition retention job? | | Infinite growth |
| U1 | Stop button during run? | | Disabled |
| U2 | Reconnect banner? | | Silent fail |
| U3 | Tool cards collapsible? | | Raw JSON wall |
| U4 | Mobile composer usable? | | |
| U5 | a11y live region? | | |

**Верdict:** Approve ≥ 8 avg, SSE replay verified.
