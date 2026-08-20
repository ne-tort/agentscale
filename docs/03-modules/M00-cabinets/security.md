# M00 — Security: кабинеты

## Модель угроз

| Угроза | Митigation |
| --- | --- |
| Cross-tenant access | RLS, JWT `tid`, middleware |
| Cross-cabinet data leak | workspace_key validation, storage sandbox |
| Privilege escalation via s4b override | DB trigger + API validation |
| Session fixation on switch | rotate session binding, audit |
| Pack seed supply chain | pack_checksum, signed packs |

## RBAC

| Role | cabinets:read | cabinets:write | cabinets:archive | switch |
| --- | --- | --- | --- | --- |
| viewer | ✓ | ✗ | ✗ | ✓* |
| operator | ✓ | ✓ | ✗ | ✓ |
| admin | ✓ | ✓ | ✓ | ✓ |

*viewer может switch только между кабинетами, к которым имеет membership.

## JWT claims

```json
{
  "sub": "user-uuid",
  "tid": "acme-corp",
  "cab": "0195a1b2-c3d4-7890-abcd-ef1234567890",
  "caps_hash": "sha256:...",
  "scp": ["cabinets:read", "projects:write"]
}
```

При switch обновляется `cab` и `caps_hash`. Downstream API отклоняет запрос если `X-Cabinet-Id` ≠ JWT `cab` (strict mode, default on).

## Capability enforcement layers

1. **Profile registry** — s4b не в schema non-electronics профилей
2. **API create** — reject overrides
3. **DB trigger** — last line of defense
4. **MCP registration** — commerce-s4b not loaded
5. **Agent AGENTS.md** — seed pack не упоминает S4B для generic

## Audit

Все действия пишутся в `cabinet_audit_log`:

- create, archive, restore, switch
- failed isolation attempts (security event)

Retention: 400 дней.

## Pack seed security

- Packs хранятся read-only в `packages/cabinet-packs/`
- Verify checksum перед распаковкой
- Запрет executable bit на seed files
- Max path depth при unzip

## Cross-cabinet negative tests (security suite)

| Test ID | Категория |
| --- | --- |
| NEG-CAB-001 … 010 | API isolation (см. domain.md) |
| NEG-CAB-ST-001 … 004 | Storage sandbox |
| NEG-CAB-MCP-001 … 004 | MCP tool isolation |
| NEG-CAB-SEC-001 | JWT cab tampering → 401 |
| NEG-CAB-SEC-002 | Replay switch without session → 401 |
| NEG-CAB-SEC-003 | Horizontal: user A switch to cabinet без membership |
| NEG-CAB-SEC-004 | Inject s4b in capabilities JSON body at PATCH → 422 |

## Compliance notes

- Архивный кабинет: данные остаются encrypted at rest (AES-256 storage)
- Export tenant data включает все `{tid}/{cid}/` prefixes по запросу DSR
