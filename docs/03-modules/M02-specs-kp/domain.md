# M02 — Домен: спеки и КП

## Run

```typescript
interface SpecRun {
  run_id: string;
  project_id: string;
  workspace_key: string;
  input_file: string;           // inbox filename
  phase: PipelinePhase;
  phase_status: 'pending' | 'running' | 'completed' | 'failed' | 'blocked';
  error?: { code: string; message: string };
  created_at: ISO8601;
  updated_at: ISO8601;
  stats: {
    rows: number;
    line_items: number;
    offers: number;
    needs_review: number;
  };
}

type PipelinePhase =
  | 'ingest'
  | 'classify'
  | 'search'
  | 'rank'
  | 'variants'
  | 'review'
  | 'final';
```

## State machine transitions

| From | To | Guard |
| --- | --- | --- |
| — | ingest | new_run |
| ingest | classify | rows.json exists |
| classify | search | lineitems.json exists |
| search | rank | offers.json exists (may be partial) |
| rank | variants | selection.json exists |
| variants | review | commerce.sqlite imported |
| review | final | operator explicit OK |
| review | search | operator re-search |
| * | blocked | phase failed irrecoverable |

Backward transitions **запрещены** автоматически (кроме operator re-search из review).

## LineItem

```typescript
interface LineItem {
  line_id: string;
  raw_text: string;
  category: string;              // mouse, monitor, psu, notebook, ...
  manufacturer?: string;
  model?: string;
  part_number?: string;
  qty: number;
  constraints: string[];
  bundle_hint?: boolean;
  confidence: number;            // 0..1
  needs_review: boolean;
  clarify_notes?: string;
}
```

## Offer

```typescript
interface Offer {
  offer_id: string;
  line_id: string;
  part_number: string;
  seller: string;
  title: string;
  price: number;
  currency: string;
  vat_included?: boolean;
  in_stock: boolean;
  source: 'catalog' | 's4b' | 'web';
  match_type: 'exact' | 'equivalent' | 'alternative';
  relevance_score: number;
  trusted_seller: boolean;
  url?: string;
  as_of: ISO8601;
}
```

## Selection (rank output)

```typescript
interface Selection {
  line_id: string;
  primary_offer_id: string;
  alternative_offer_ids: string[];
  rationale?: string;
}
```

## Equipment card (electronics only)

```typescript
interface EquipmentCard {
  equipment_id: string;
  part_number?: string;
  category: string;
  specs: Record<string, string | number>;
  compatibility_notes?: string[];
  analogs?: string[];
  source_run_id?: string;
}
```

## Search cascade (domain policy)

1. **catalog** — user + local DB (M04)
2. **s4b** — только electronics-procurement, in_stock only
3. **web** — allowlisted shops

## Primary selection rules

- Same P/N + trusted seller → primary = min price
- No P/N → best match, then min price
- Untrusted cheaper → alternative, not primary

## Инварианты

| ID | Инвариант |
| --- | --- |
| INV-SKP-001 | Цена только из search artifacts |
| INV-SKP-002 | phase advance требует output file predecessor |
| INV-SKP-003 | S4B offers только in_stock |
| INV-SKP-004 | final только из review + operator flag |
| INV-SKP-005 | equipment_cards только если capabilities.equipment_cards |
| INV-SKP-006 | input/ immutable после classify start |
| INV-SKP-007 | Один run_id — один input file |

## Negative test IDs

| Test ID | Сценарий |
| --- | --- |
| NEG-SKP-001 | Advance to rank без offers.json |
| NEG-SKP-002 | S4B on_order в offers |
| NEG-SKP-003 | final без operator |
| NEG-SKP-004 | equipment_cards на generic cabinet |
| NEG-SKP-005 | Agent writes kp.xlsx directly |
| NEG-SKP-006 | Modify customer P/N in classify |
| NEG-SKP-007 | Search S4B on non-electronics |
