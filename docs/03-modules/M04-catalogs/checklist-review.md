# M04 — Checklist: review

## System databases (system-databases spec)

- [ ] s4b-cache documented and implemented as non-deletable
- [ ] electronics-procurement only — verified E2E
- [ ] Generic cabinet: NO s4b in MCP, API, UI
- [ ] DELETE system DB always 403

## Credential vault

- [ ] No secrets in PG, logs, API responses
- [ ] All four cred states reachable and UI badges correct
- [ ] rate_limited blocks live S4B, allows cache read
- [ ] credentials_invalid blocks live fetch

## User catalogs

- [ ] Indexing failure shows failed status + error
- [ ] Cross-cabinet catalog isolation
- [ ] Trusted flag affects M02 primary selection

## Security

- [ ] NEG-CAT-SEC-001 … 004 pass
- [ ] Vault KMS rotation documented
- [ ] SQL allowlist enforced

## Integration with M02

- [ ] Search cascade catalog → s4b* → web
- [ ] sources.log reflects cred state skips
- [ ] on_order never in offers

## Sign-off

| Role | OK |
| --- | --- |
| Security | |
| Backend | |
| Domain (закупки) | |
