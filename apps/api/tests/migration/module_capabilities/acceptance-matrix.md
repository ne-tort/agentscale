# MIG-MODULE-01 acceptance matrix

Tests are authored but intentionally not executed under the migration verification freeze.

| Contract | Acceptance artifact | Expected outcome |
|---|---|---|
| Stable schema/field IDs | `test_legacy_module_normalizes_to_typed_ids_and_canonical_digest` | IDs are carried into schema, MCP and materialize declarations |
| Canonical digest | `test_canonical_digest_is_stable_for_object_key_order` | Normalized candidate has a SHA-256 content digest; declaration ordering remains explicit |
| Missing identity | `test_missing_schema_identity_is_quarantined_with_reason` | No candidate; stable quarantine code/path |
| First-class refs | `test_missing_reference_field_identity_is_quarantined` | No `$row_id`/implicit ref fallback |
| Unsupported DSL | `test_unsupported_legacy_action_kind_is_quarantined` | No action dispatch or positive candidate |
| Legacy secret expansion | `test_unsupported_legacy_foreach_secret_shape_is_quarantined` | Quarantine with reason, no secret material copied |
| MCP package boundary | MCP server tests | IDs, transport and credential headers are explicit; raw auth is quarantined |
| Dependencies/conflicts | dependency and semver acceptance tests | Missing, unpublished, conflicting and cyclic graphs are non-publishable; unknown constraints fail closed |
| Durable scope gates | lifecycle acceptance tests | No repository/verifier call when owner/version/scope gate is absent |
| Immutable snapshot | lifecycle acceptance tests | Published snapshot pins digest and transitions; changed-digest commit conflicts |
| Explicit deprecation | lifecycle acceptance tests | Forward transition requires actor, reason, evidence and correlation |
| Draft staging | pipeline acceptance tests | Valid draft goes through draft port; invalid identity/dependency goes quarantine |

The matrix does not authorize execution, persistence, API registration, legacy writes,
runtime materialization, positive publishing, cutover, or formal verification.
