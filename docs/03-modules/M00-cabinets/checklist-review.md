# M00 — Checklist: review

## Домен и инварианты

- [ ] INV-CAB-001 … 007 покрыты тестами или кодом
- [ ] profile_id immutable — нет скрытых PATCH путей
- [ ] s4b только electronics — проверено на API, DB, MCP, UI уровнях

## API

- [ ] OpenAPI соответствует реализации
- [ ] Ошибки возвращают stable `code` + `request_id`
- [ ] Rate limits documented и enforced
- [ ] Switch atomic — нет окна с mixed cid в parallel requests

## Безопасность

- [ ] RLS включён на staging/prod
- [ ] Cross-tenant penetration test passed
- [ ] Audit log не содержит PII лишнего
- [ ] Pack checksum verified in CI

## Storage

- [ ] Path traversal tests green
- [ ] Symlink attack rejected
- [ ] Quota enforcement работает

## UX

- [ ] Switch с unsaved M03 — confirm
- [ ] S4B badge только на electronics
- [ ] Archive блокирует новые операции

## Observability

- [ ] Metrics: `cabinet_created_total`, `cabinet_switch_total`, `seed_failed_total`
- [ ] Traces: switch span links to session
- [ ] Alerts on seed_failed spike

## Regression (negative suite)

- [ ] Все NEG-CAB-* из domain/security зелёные в CI
- [ ] Manual: два кабинета, два проекта, нет cross-read

## Sign-off

| Роль | Имя | Дата | OK |
| --- | --- | --- | --- |
| Backend | | | |
| Security | | | |
| QA | | | |
