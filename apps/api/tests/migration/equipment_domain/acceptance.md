# MIG-EQ-02 acceptance fixture index

The fixtures and executable acceptance tests cover typed domain records and the port-driven flow: ordered JSONB backfill, scoped projections, supplier/source-separated offers, human-only selection and supersession, stale/unknown availability policy, manual overrides, selected-line procurement totals, source-generation fencing, scoped export idempotency, replay/conflict and uncertain-commit reconciliation. Tests intentionally remain unrun under the migration freeze.

See `apps/api/src/prodavan/application/migration/equipment_domain/acceptance.md` for acceptance and promotion gates. No fixture represents acquired source data or authorizes promotion.
