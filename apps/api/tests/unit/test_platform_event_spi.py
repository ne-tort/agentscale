"""Unit tests — platform event SPI helpers."""

from prodavan.application.cabinets.platform_event_spi import package_subscribes_to_event


def test_package_subscribes_wildcard() -> None:
    assert package_subscribes_to_event({"platform_events": ["*"]}, "company.suspended")


def test_package_subscribes_specific() -> None:
    manifest = {"platform_events": ["company.suspended", "employee.disabled"]}
    assert package_subscribes_to_event(manifest, "company.suspended")
    assert not package_subscribes_to_event(manifest, "project.created")


def test_package_subscribes_on_platform_event_alias() -> None:
    assert package_subscribes_to_event({"on_platform_event": "employee.disabled"}, "employee.disabled")


def test_package_subscribes_absent() -> None:
    assert not package_subscribes_to_event({"tools": []}, "company.suspended")
