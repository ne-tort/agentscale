"""Cabinet rows validation unit tests (L06)."""

import pytest

from prodavan.domain.errors import AppError
from prodavan.infrastructure.cabinets.sql import data_table_slug, qident


def test_qident_valid() -> None:
    assert qident("data_suppliers") == '"data_suppliers"'


def test_qident_rejects_injection() -> None:
    with pytest.raises(AppError) as ei:
        qident('x"; DROP SCHEMA public; --')
    assert ei.value.code == "VALIDATION_ERROR"


def test_data_table_slug() -> None:
    assert data_table_slug("suppliers") == "data_suppliers"
