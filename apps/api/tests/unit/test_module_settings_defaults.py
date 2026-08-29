"""Unit tests — default module profile selection for projects."""

from prodavan.application.project_service.module_settings import pick_default_profile_row_id


def test_pick_default_profile_prefers_name_default() -> None:
    profiles = [
        {"row_id": "b", "body": {"name": "Beta"}},
        {"row_id": "a", "body": {"name": "Default"}},
        {"row_id": "c", "body": {"name": "Alpha", "is_default": True}},
    ]
    assert pick_default_profile_row_id(profiles) == "a"


def test_pick_default_profile_uses_is_default_when_no_default_name() -> None:
    profiles = [
        {"row_id": "z", "body": {"name": "Zeta"}},
        {"row_id": "m", "body": {"name": "Main", "is_default": True}},
        {"row_id": "a", "body": {"name": "Alpha"}},
    ]
    assert pick_default_profile_row_id(profiles) == "m"


def test_pick_default_profile_alphabetical_fallback() -> None:
    profiles = [
        {"row_id": "z", "body": {"name": "Zeta"}},
        {"row_id": "a", "body": {"name": "Alpha"}},
    ]
    assert pick_default_profile_row_id(profiles) == "a"
