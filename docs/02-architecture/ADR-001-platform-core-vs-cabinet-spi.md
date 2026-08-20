# ADR-001: Platform Core vs Cabinet SPI

**Status:** Accepted  
**Date:** 2026-08-21  
**Supersedes (partial):** in-process-only Domain Plugin framing in [platform-extensibility.md](platform-extensibility.md) where it conflicts with DB-per-cabinet.

## Context

Prodavan must become a universal aggregator: many **cabinets** (task-specific modules) plug into one **platform** (users, projects, AI agent/chat/prompts). Business logic must not live in core. Each cabinet owns its business database.

## Decision

### Platform Core (frozen)

Owns only:

| Layer | Responsibility |
|-------|----------------|
| User / Tenant | Identity, JWT, memberships, `platform.admin` |
| Cabinet Registry / Host | Cabinet lifecycle metadata, pack binding, SPI routing |
| Project Workspace | Project CRUD, FS layout contract (`inbox/`, `runs/`, …), ACL |
| Agent / Chat / Prompts | Session lifecycle, chat stream, prompt tree framework, MCP ACL engine |
| Platform Events | `project.created`, `file.uploaded`, `agent.tool_requested`, … |

**Platform DB** (`schema tenants` today): users, tenants, memberships, cabinets (metadata + `profile_id` + pack version + SPI endpoint), projects (metadata), agent sessions, prompt version metadata.

Core **must not** contain: S4B filters, KP export rules, line-item classifiers, offer ranking, equipment cards domain tables.

### Cabinet Module

A cabinet is an isolated product implementing **Cabinet SPI** (see [cabinet-spi.md](cabinet-spi.md)):

- Own **Application / Domain / Infrastructure** layers
- Own **database** per cabinet *instance* (Postgres schema `cab_{cabinet_id}` or dedicated SQLite/Postgres URL recorded on `cabinets.db_dsn`)
- Own Flutter UI module refs + prompts + MCP tool bindings
- Pack migrations run via `POST /migrate` on SPI, never via core Alembic

Deploy may be colocated (in-process adapter) or remote HTTP; **the SPI contract is identical**.

### Dependency rule

```text
Flutter Shell  →  Platform API
Cabinet UI     →  Platform API (auth/project) + Cabinet SPI (domain)
Platform Host  →  Cabinet SPI
Cabinet        →  Platform ports only (workspace paths, events ack) — no core ORM imports
```

## Consequences

1. Existing `application/pipeline|catalogs|integrations` are **cabinet-candidate** and move under `prodavan.cabinets.electronics_procurement`.
2. Core routes `/specs`, `/catalogs` become thin facades that dispatch via Cabinet Registry.
3. Second cabinet (`generic-assistant`) proves core has no procurement imports.
4. Docs [platform-extensibility.md](platform-extensibility.md) remain valid for packs; Domain Plugin = SPI implementation of a cabinet module.
