"""Build official starter bundle zips (L04) — version-controlled fixtures."""

from __future__ import annotations

from prodavan.infrastructure.cabinets.bundle_codec import pack_bundle

_EQUIPMENT_TABLE_ID = "tbl_equipment_line_items"
_EQUIPMENT_VIEW_ID = "view_equipment_line_items"
_EQUIPMENT_TAB_ID = "tab_equipment_line_items"


def build_equipment_procurement_bundle() -> bytes:
    """Commerce-style procurement seed: line_items table + collection tab."""
    tables = [
        {
            "id": _EQUIPMENT_TABLE_ID,
            "slug": "line_items",
            "label": "Line items",
            "storage_kind": "physical",
            "status": "active",
        }
    ]
    columns = [
        {
            "id": "col_li_title",
            "table_id": _EQUIPMENT_TABLE_ID,
            "table_slug": "line_items",
            "name": "title",
            "col_type": "text",
            "required": True,
            "unique_col": False,
            "ref_table_slug": None,
        },
        {
            "id": "col_li_pn",
            "table_id": _EQUIPMENT_TABLE_ID,
            "table_slug": "line_items",
            "name": "part_number",
            "col_type": "text",
            "required": False,
            "unique_col": False,
            "ref_table_slug": None,
        },
        {
            "id": "col_li_qty",
            "table_id": _EQUIPMENT_TABLE_ID,
            "table_slug": "line_items",
            "name": "qty",
            "col_type": "number",
            "required": False,
            "unique_col": False,
            "ref_table_slug": None,
        },
        {
            "id": "col_li_status",
            "table_id": _EQUIPMENT_TABLE_ID,
            "table_slug": "line_items",
            "name": "status",
            "col_type": "text",
            "required": False,
            "unique_col": False,
            "ref_table_slug": None,
        },
    ]
    views = [
        {
            "id": _EQUIPMENT_VIEW_ID,
            "slug": "line_items",
            "table_slug": "line_items",
            "ui_json": {"version": 1, "kind": "collection", "title_field": "title"},
            "version": 1,
        }
    ]
    tabs = [
        {
            "id": _EQUIPMENT_TAB_ID,
            "title": "Line items",
            "order": 15,
            "view_id": _EQUIPMENT_VIEW_ID,
            "system": False,
        }
    ]
    data_by_slug = {
        "line_items": [
            {
                "title": "Example switch 24-port",
                "part_number": "WS-C2960-24TC-L",
                "qty": 2,
                "status": "needs_review",
            }
        ]
    }
    return pack_bundle(
        name="Equipment procurement",
        exported_from_cabinet_id="starter:equipment-procurement",
        tables=tables,
        columns=columns,
        tabs=tabs,
        views=views,
        data_by_slug=data_by_slug,
    )
