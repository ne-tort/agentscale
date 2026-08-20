# M03 — Storage: промпты

## Layout

```text
storage/cabinets/{tid}/{cid}/prompts/
├── AGENTS.md
├── profiles/
│   └── kp/
│       ├── README.md
│       ├── 01-ingest.md
│       ├── 02-classify.md
│       ├── 03-clarify.md
│       ├── 04-search.md
│       ├── 05-rank.md
│       ├── 06-output.md
│       └── 07-equipment.md
├── skills/
│   └── commerce-search/
│       └── SKILL.md
├── .prompts-version.json
└── .versions/
    └── ver_*/
```

## Pack seed (from M00)

Source: `packages/cabinet-packs/{profile_id}/prompts/**`

Electronics pack includes full `profiles/kp/` aligned with Commerce repo.

Generic pack: minimal AGENTS.md without S4B/commerce references.

## Export zip structure

```text
prompts-export-{cid}-{ts}.zip
├── manifest.json           # export metadata
├── AGENTS.md
├── profiles/
└── skills/
```

Import validates paths against allowlist.

## File constraints

| Constraint | Value |
| --- | --- |
| Allowed extensions | .md, .json |
| Max file size | 512 KiB |
| Max tree depth | 8 |
| Max files | 1000 |

## Agent runtime read

On cabinet switch:

1. Load AGENTS.md into system context
2. Load active profile README (from capabilities default_profile_path)
3. Skills lazy-loaded on demand

Path: `prodavan://storage/cabinets/{tid}/{cid}/prompts/AGENTS.md`

## Negative test IDs

| Test ID | Сценарий |
| --- | --- |
| NEG-PRM-ST-001 | Write outside prompts/ prefix |
| NEG-PRM-ST-002 | Binary file upload |
| NEG-PRM-ST-003 | Version dir traversal |
