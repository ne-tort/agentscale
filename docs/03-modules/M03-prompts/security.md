# M03 — Security: промпты

## Path traversal

- Normalize path, reject `..`, absolute paths, null bytes
- Zip slip protection on import

## Content safety

- Markdown rendered with sanitization (no raw HTML script)
- Links: rel=noopener, optional domain allowlist for preview

## RBAC

| Action | viewer | operator | admin |
| --- | --- | --- | --- |
| read | ✓ | ✓ | ✓ |
| write file | ✗ | ✓ | ✓ |
| import replace | ✗ | ✗ | ✓ |
| rollback prod | ✗ | ✗ | ✓ |

## Prompt injection awareness

AGENTS.md is trusted operator content — not end-user spec input. Still:

- Audit log on all writes
- Optional second-person review for import replace

## Export tokens

Download URLs single-use or short TTL (15 min), bound to user session.

## Cross-cabinet

Prompts storage under `{cid}` — same isolation as M00.

## Negative test IDs

| Test ID | Focus |
| --- | --- |
| NEG-PRM-SEC-001 | Zip slip |
| NEG-PRM-SEC-002 | XSS in preview |
| NEG-PRM-SEC-003 | IDOR export other cid |
