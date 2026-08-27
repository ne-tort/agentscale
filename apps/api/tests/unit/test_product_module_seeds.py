"""Unit tests for product module seed definitions."""

from prodavan.application.platform.product_module_seeds import (
    EXAMPLE_MODULE_IDS,
    PRODUCT_MODULES,
    mod_files_meta,
    mod_mcp_meta,
    mod_prompts_meta,
)


def test_product_modules_replace_examples() -> None:
    ids = {m[0] for m in PRODUCT_MODULES}
    assert ids == {"mod_prompts", "mod_mcp", "mod_files"}
    assert len(EXAMPLE_MODULE_IDS) == 4


def test_prompts_meta_has_materialize_and_seed() -> None:
    meta = mod_prompts_meta()
    assert any(t["slug"] == "prompt_profiles" for t in meta["tables"])
    assert meta["materialize"]
    assert meta["seed_rows"]["items"]


def test_files_meta_has_file_ref_column() -> None:
    meta = mod_files_meta()
    cols = meta["columns"]
    assert any(c["name"] == "file_ref" and c["type"] == "file_ref" for c in cols)


def test_mcp_meta_has_zip_materialize_rule() -> None:
    meta = mod_mcp_meta()
    rules = meta["materialize"]
    assert any(r["target"]["format"] == "mcp_package" for r in rules)
