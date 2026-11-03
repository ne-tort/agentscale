# Equipment procurement feature

This feature is an isolated React surface for the equipment workflow:

`Requests → Candidate groups → Offers/review/selection → Procurement/Budget → Export`

It is intentionally contained under `apps/web/src/features/equipment/**`. It does not add a route, alter the shared API client, change app context, or register an endpoint. `EquipmentProcurementPage` presents a workspace navigation between the five procurement views and Source configuration, with scope, offer freshness, selected-line count, budget total, and open blockers in view. It accepts an injected `EquipmentLocalService`; its default implementation is an in-memory typed adapter with an empty workspace, so the UI cannot imply live procurement data.

The Source configuration area mounts the existing `EquipmentSourcesPage` only when a host injects `createEquipmentSourcesService({ getAccessToken })` through `sourcesService`; the screen loads fresh metadata/catalog rows itself. Without that service, `initialSourcesWorkspace` is rendered as a clearly read-only snapshot. Catalog rows describe ingestion configuration, not authoritative offer evidence. The project route currently supplies neither the privileged admin service nor project scope; until that route owner supplies them, the page reports the missing adapter and does not issue admin requests. The feature does not read global auth/context itself.

## Semantic boundaries

- `EquipmentRequestLine` is the customer requirement and the unit of selection.
- `EquipmentCandidateGroup` is agent intent. `matchKind` stays visible as `exact`, `analog`, or `doubt`; group keys and notes cannot author price, supplier, currency, stock, or source facts.
- `EquipmentAuthoritativeOffer` is platform materialization. The UI requires supplier, source, original price/currency, RUB conversion state, freshness, observed time, and stale state to be represented separately. An unknown amount stays `null` and renders as unresolved. Optional source/run/artifact/snapshot/hash/evidence IDs are carried for the migration handoff when the adapter supplies them.
- `selectOffer` is a human action. It validates the line/offer relationship, rejects stale or unknown-freshness offers, stores one selected offer per request line, and appends a small local audit record. Clearing is explicit.
- Procurement totals are derived only from selected offers. Unknown prices, stale selections, missing source provenance, and missing delivery remain blockers; they never become zero.
- Seller margin, delivery, and include-delivery are local manual procurement inputs. The service records `marginSource` and `manualFields` so a later server sync can preserve overrides.
- CSV export is local and provenance-preserving. It includes request/offer IDs, source/run/artifact/snapshot/evidence references, observed time, freshness/stale reason, original currency, RUB value, manual offer and procurement overrides, budget totals, selection status, and warnings.

## Migration-backed blockers

The feature intentionally leaves these integrations unresolved until the migration contracts are implemented by the platform boundary:

1. The procurement page still uses the local adapter on the existing route. Its current service contract does not expose server-resolved tenant/cabinet/project/session scope, and no live procurement HTTP endpoint is invented here.
2. The migration audit requires immutable snapshot/offer provenance (`source_id`, ingestion run, artifact, snapshot, observed time, digest) and fail-closed visibility checks. The local types carry these fields where known, but do not claim server authorization.
3. Selection and procurement need a trusted transaction with human approval, membership proof, idempotency, and writer-generation/CAS fencing. The local service models the UI semantics only.
4. OpenSearch remains a rebuildable search projection; this feature does not read index names or treat search hits as authoritative offers.
5. A real export endpoint/file asset contract is not available in this web shell. The current CSV is a reviewable local artifact and must be replaced or wrapped by a typed file-ref action when that contract lands.
6. The project equipment route does not yet inject the existing admin catalog service or a scoped procurement service. Sources are therefore unavailable/read-only in that mount. Scope is not surfaced by the current procurement service contract. No source pause/resume endpoint exists; the source page correctly keeps those controls disabled because a row PATCH can auto-index.

The implementation follows `docs/PRODUCT.md` and `docs/migration-audit-equipment.md` as semantic references. Tests are acceptance fixtures and are intentionally not executed during the migration verification freeze.
