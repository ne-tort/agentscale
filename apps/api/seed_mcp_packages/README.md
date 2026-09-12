# Seed MCP packages

Platform-shipped MCP zips that are uploaded to object storage on API startup and
attached to product module rows when `file_ref` is empty (user replace wins).

## Layout

```
seed_mcp_packages/
  README.md
  prodavan-equipment/
    manifest.json          # mcp.package manifest (name/version/entry/tools)
```

Zip **bytes are not committed**. `SeedMcpBootstrapService` builds the zip at runtime from:

- `manifest.json` in this folder (copied into the API image as `/app/seed_mcp_packages/`)
- `application/mcp/prodavan_equipment_mcp/server.py`
- `application/modules/equipment_catalog_search.py`

If the folder is missing at runtime, the builder falls back to a code-derived
manifest (`platform_equipment_mcp_package`) so attach still works.

MinIO/local key: `platform/seed-mcp/{name}-{version}.zip`

## Adding a package

1. Create `seed_mcp_packages/<name>/manifest.json` (`format: mcp.package`).
2. Register the package in `seed_mcp_builder.SEED_PACKAGES`.
3. Wire attach target (module table/row) in `SeedMcpBootstrapService`.
