# Second cabinet proof

`generic-assistant` (pack dir `_template`) is registered in
`prodavan.cabinets.registry` alongside `electronics-procurement`.

## Proof checklist

1. `GET /cabinet-profiles` lists both profiles.
2. `POST /cabinets` with `profile_id=generic-assistant` seeds `_template`, runs SPI migrate → `cabinet.sqlite`.
3. Specs/catalog endpoints return `CABINET_MODULE_UNSUPPORTED` (no pipeline module).
4. Flutter create-cabinet dialog offers profile picker; project opens with tab `chat` only.
5. `GET /cabinets/{id}/spi/health` → `pack_id=generic-assistant`.
6. Unit: `tests/unit/test_cabinet_registry.py`.

Core must not import `prodavan.cabinets.electronics_procurement.pipeline` from platform packages (`platform/`, `api/v1/auth|projects|prompts`).
