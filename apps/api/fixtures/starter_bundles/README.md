# Official starter bundle zip files (L04)

Files: `{bundle_id}.bundle.zip`

Generated from `prodavan.domain.admin.starter_bundle_factory` — regenerate with:

```bash
cd apps/api
python -c "from pathlib import Path; from prodavan.domain.admin.starter_bundle_factory import build_equipment_procurement_bundle; p=Path('fixtures/starter_bundles/equipment-procurement.bundle.zip'); p.parent.mkdir(parents=True, exist_ok=True); p.write_bytes(build_equipment_procurement_bundle())"
```
