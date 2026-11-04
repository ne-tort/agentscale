``EvidenceStore`` — :class:`SqlM04EEvidenceStore` over the four registered
ledger tables revision ``2026110315`` created (see its own section below).

Why the evidence store is a ledger and not the promotion table
=============================================================

``EvidenceStore.classify_and_commit_page`` needs, in **one** transaction: a
per-source first-mapping ledger, quarantine/conflict observations carrying the
*observed* digest, redacted audit, and a durable cursor advanced by CAS. The two
older M04E tables (revision ``2026110302``) cannot carry any of it:

* ``m04e_dynamic_app_definitions`` / ``m04e_dynamic_app_versions`` are canonical
  identity rows keyed by ``(id, tenant_id)`` / ``(id, definition_id)``; neither has
  ``source_kind``, ``source_schema_version``, ``observed_*``, ``reason_code``,
  ``correlation_id``, ``checkpoint`` or a sequence column;
* ``m04e_definition_version_promotion_evidence`` is a **per-partition** record
  (``uq_m04e_promotion_revision`` = ``(tenant_id, definition_id, revision_id)``)
  whose ``outcome`` is constrained to ``('blocked', 'quarantined')``
  (``ck_m04e_promotion_outcome``), so it cannot record an accepted mapping, and its
  ``source_digest``/``target_digest``/``current_version_resolution_digest``/
  ``artifact_equality_digest`` are ``NOT NULL`` sha256 gate proofs that a blocked
  page has no truthful value for.

Two tempting workarounds stay **rejected**, because both invent a schema: (a)
packing a per-source ledger and a cursor into the ``gate_evidence`` JSONB column of
a promotion record, and (b) treating the target foundation rows as the mapping
ledger and synthesising the checkpoint/correlation fields ``BackfillRecord``
requires. A store that answered ``find_existing`` with ``None`` because it could
not see its own ledger would license a second append for an already-mapped source
identity — the opposite of the fail-closed rule. The reviewed replacement is the
four-table ledger ``m04e_definition_version_{mappings,quarantine,checkpoints,audit}``
(mirroring ``scope_owner_mappings`` / ``scope_owner_quarantine`` /
``scope_owner_audit``), which is what :class:`SqlM04EEvidenceStore` reads and
writes. The promotion table keeps its own job: it is the ``MIG-M04E-01`` gate
record, still per-partition and still two-state, which is why that gate still
refuses by name (see :data:`EVIDENCE_STORE_SCHEMA_GAPS`).
