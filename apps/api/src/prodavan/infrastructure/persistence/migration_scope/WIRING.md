# M04 persistence adapter wiring

`adapter.py` provides `SqlAlchemyTrustedScopeTransactionPort`, but it is not constructed by an application composition root and must not be treated as production-wired. The default `rls_manifest=None` is intentional: there is no accepted M04 PostgreSQL policy/role review in this zone.

Current schema blockers prevent positive mapping and resolution even if the adapter is imported: no checkpoint model/revision, no canonical schema lineage on source mapping, no installed M04 RLS/append-only privileges, unresolved membership lifecycle and parent policy, and no binding between the opaque application transaction ID and the adapter-owned PostgreSQL transaction.

The adapter returns no active scope and does not advance a checkpoint or write mapping, quarantine, or audit evidence while these gates are absent. Do not bypass the gate with a guessed checkpoint table, policy digest, schema creation, or widened role grant. Promote wiring only after the promotion handoff gates are closed and the production transaction is independently reviewed.