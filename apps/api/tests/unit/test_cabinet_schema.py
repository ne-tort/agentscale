"""Cabinet domain unit tests (L06)."""

import pytest

from prodavan.domain.cabinets import schema_name_for_instance


def test_schema_name_from_instance_id() -> None:
    assert schema_name_for_instance("cab_abc123def4567890") == "cab_inst_abc123def4567890"


def test_schema_name_rejects_bad_id() -> None:
    with pytest.raises(ValueError):
        schema_name_for_instance("bad id with spaces")
