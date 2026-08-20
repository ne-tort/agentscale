# M02 — Checklist: review

## Domain

- [ ] All phase transitions match domain.md
- [ ] INV-SKP-001 … 007 tested
- [ ] Primary/alternative rules verified on sample spec

## Commerce alignment

- [ ] AGENTS.md prohibitions reflected in code
- [ ] No on_order in offers.json
- [ ] KP only via export API

## S4B scope

- [ ] commerce-s4b absent on generic cabinet (MCP + API + UI)
- [ ] s4b-cache DB invisible without capability

## Data integrity

- [ ] input/ immutable after classify
- [ ] Re-search preserves run_id audit trail

## UX

- [ ] Timeline accurate during long search
- [ ] needs_review visible before finalize
- [ ] Rate limit banner from M04

## Observability

- [ ] Metrics: runs_created, phase_duration_seconds, offers_total
- [ ] Alert on phase_failed rate

## Sign-off

| Role | OK |
| --- | --- |
| Domain expert (закупки) | |
| Backend | |
| QA | |
