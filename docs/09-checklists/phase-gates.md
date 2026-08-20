# Phase gates (P0–P9)

| Phase | Gate | Criteria |
|-------|------|----------|
| P0 | Git ready | main clean; branch `prodavan/platform-foundation` |
| P1 | Repo + submodule | `ne-tort/prodavan` exists; submodule clones |
| P2 | Architecture | 8 ADR files + threat-model ≥ 8/10 each |
| P3 | Modules | M00–M09 × 10 files; module avg ≥ 8; S4B ≥ 8 |
| P4 | Agent runtime | providers + spikes documented ≥ 7 |
| P5 | Frontend | architecture, design-system, widgets, screens ≥ 8 |
| P6 | Backend | ERD + RLS ≥ 8 |
| P7 | Infra | topology, runner outside k3s ≥ 8 |
| P8 | Pack + migration | pack.json + commerce-semantics ≥ 8 |
| P9 | Audit | Doc avg ≥ 8.5; zero Doc items below 6 |

## Implementation gates (I0–I9)

| Gate | Criteria |
|------|----------|
| I0 start | Doc P9 signed; Impl overall ≥ 6.5; blockers BL-02/03 planned |
| I0 done | FastAPI + Flutter scaffold; `ci-schemas.yml` green |
| I2 done | Pack seed e2e; capability registry frozen (BL-04) |
| I6 done | Agent SSE + MCP gateway; BL-01 closed or Cursor-only v1 |
| I7 done | Staging deploy from GH runner |
| Platform v0.1 | Impl avg ≥ 8; M00–M07 modules Impl ≥ 7 |

See [`10-implementation/roadmap.md`](../10-implementation/roadmap.md).

## Sign-off

| Phase | Date | Score avg | Reviewer |
|-------|------|-----------|----------|
| P0 | 2026-08-20 | 10 | agent |
| P1 | | | |
| P2 | | | |
| P3 | | | |
| P4 | | | |
| P5 | | | |
| P6 | | | |
| P7 | | | |
| P8 | | | |
| P9 | | | |
