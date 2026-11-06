# Owner inventory analyzer

`migration_owner_inventory` is a persistence-agnostic, read-only analyzer for
owner records/rows during the `String(80)` to `String(40)` migration.

## Contract

Call `analyze_owner_inventory(rows, references)` with typed ports implementing
`OwnerRowPort` and `ReferencePort`. Ports supply declarative rows and snapshots
of owners, cabinets, and projects; the analyzer does not accept a database
connection, ORM session, or write callback.

Each row gets either:

- a `candidate` containing the original owner ID when it is a non-empty,
  whitespace-exact string of at most 40 characters and all references are live,
  present, same-tenant, and cabinet/project compatible; or
- evidence describing malformed/overlength IDs, orphan/deleted/cross-tenant
  ownership, missing cabinet/project references, or cabinet/project mismatch.

Any evidence also adds `quarantine_required`. The original `owner_id` remains
available in the finding. No truncation, implicit cast, repair, deletion,
quarantine write, or positive write is performed.

`FindingCode.SAFE_CANDIDATE` is informational evidence for an otherwise clean
row. A candidate is not a migration instruction; callers must perform an
explicit, separately reviewed write if one is eventually approved.

## Deliberate non-scope

There is no ORM/Alembic integration, runtime wiring, DB access, schema change,
or mutation API in this zone.
