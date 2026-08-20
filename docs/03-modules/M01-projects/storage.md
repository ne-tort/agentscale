# M01 — Storage: проекты

## Полный layout

```text
storage/cabinets/{tid}/{cid}/projects/{pid}/
├── project.json
├── commerce.sqlite
├── inbox/
│   ├── spec-2026-08-20.xlsx
│   ├── spec-2026-08-20.xlsx.extracted.md   # auto для xlsx/csv
│   └── ...
├── runs/
│   └── {run_id}/
│       ├── status.json
│       ├── input/                 # копия из inbox
│       ├── rows.json
│       ├── lineitems.json
│       ├── offers.json
│       ├── selection.json
│       ├── sources.log
│       └── needs-review.json
└── export/
    ├── kp-{run_id}-{ts}.xlsx
    └── ...
```

## inbox/

| Правило | Описание |
| --- | --- |
| Имена файлов | Оригинальные от оператора, без переименования |
| Formats | xlsx, xls, csv, txt (M02) |
| Extracted | `.extracted.md` для табличных форматов |
| Max size | 25 MiB per file (configurable) |

Upload: `POST /v1/projects/{pid}/inbox` (multipart) → M02 trigger optional.

## runs/

| Правило | Описание |
| --- | --- |
| run_id | ULID или `run_{timestamp}_{short}` |
| Immutability | `input/` read-only после phase ≥ classify |
| Isolation | только текущий pid |
| Concurrent | два оператора — разные run_id; один run_id — lock |

## export/

| Правило | Описание |
| --- | --- |
| KP files | Генерирует M02 `/кп` equivalent API |
| Retention | 180 days default |
| Naming | `kp-{run_id}-{ISO8601 compact}.xlsx` |

## Workspace resolution

Given session `(tid, cid, pid)`:

```python
root = f"storage/cabinets/{tid}/{cid}/projects/{pid}/"
workspace_key = f"cab:{tid}:{cid}:{pid}"
```

Agent CLI (legacy Commerce compat):

```bash
python ../../tools/new_run.py --input inbox/file.xlsx --runs-dir runs
# cwd = project root
```

## Quotas

| Resource | Default |
| --- | --- |
| Projects per cabinet | 100 |
| Runs per project | unlimited (soft 10k warning) |
| inbox total | 1 GiB |
| export total | 5 GiB |

## Negative test IDs (storage)

| Test ID | Сценарий |
| --- | --- |
| NEG-PRJ-ST-001 | Write run в чужой pid |
| NEG-PRJ-ST-002 | Delete input/ после classify |
| NEG-PRJ-ST-003 | Symlink inbox → /etc |
