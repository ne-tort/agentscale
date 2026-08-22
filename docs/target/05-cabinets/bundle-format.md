# Cabinet bundle — export / import

Переносимый артефакт кабинета между сотрудниками и компаниями (копия).

## Format (v1)

```text
cabinet.bundle-v1.zip
  manifest.json
  meta/
    tables.json
    columns.json
    tabs.json
    views.json
    mcp_tools.json         # declarative wrappers
  mcp_packages/          # embedded mcp.package-v1.zip files
  data/                  # optional seeds
    <table_slug>.jsonl
  README.md                 # optional, human
```

### manifest.json

```json
{
  "format": "cabinet.bundle",
  "format_version": 1,
  "name": "Подбор оборудования",
  "base_version": "1.0.0",
  "created_at": "…",
  "exported_from_cabinet_id": "cab_…",
  "content_hash": "sha256:…"
}
```

## Rules

| Rule | Meaning |
|------|---------|
| Import | Creates **new** CabinetInstance + **new** schema (peer-isolated copy) |
| MCP packages | Allowed as validated `mcp_packages/*.zip` only ([mcp-packages](mcp-packages.md)) |
| Loose binaries | Forbidden outside package format |
| Size | Soft/hard quotas |
| Validate | Meta schema, ui_json, package manifests |
| Secrets | Stripped on export |

## Official starter bundles

Platform may ship read-only starters (e.g. equipment-procurement seed) in catalog — same format as user export.

## UX

- Employee: Export / Import via EntityCollection actions (icon) + file picker.  
- Laconic labels: «Экспорт», «Импорт».
