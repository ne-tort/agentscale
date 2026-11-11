"""Offline checks for IPv4-preferred egress on the self-hosted runner VM."""

from __future__ import annotations

import importlib.util
from pathlib import Path

_BASE_SETUP = Path(__file__).resolve().parents[2] / "github-runner-vm/01-base.py"
_SPEC = importlib.util.spec_from_file_location("runner_base_setup", _BASE_SETUP)
assert _SPEC is not None and _SPEC.loader is not None
base_setup = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(base_setup)


def test_gai_conf_is_created_with_ipv4_precedence(tmp_path: Path) -> None:
    config = tmp_path / "gai.conf"

    base_setup.ensure_ipv4_egress_preference(config)

    text = config.read_text(encoding="utf-8")
    assert "Docker/Go" in text
    assert text.endswith(f"{base_setup.IPV4_GAI_PRECEDENCE}\n")


def test_gai_conf_preserves_policy_and_is_idempotent(tmp_path: Path) -> None:
    config = tmp_path / "gai.conf"
    config.write_text("# local policy\nprecedence ::/0 40", encoding="utf-8")

    base_setup.ensure_ipv4_egress_preference(config)
    after_first_run = config.read_text(encoding="utf-8")
    base_setup.ensure_ipv4_egress_preference(config)

    assert config.read_text(encoding="utf-8") == after_first_run
    assert after_first_run.startswith("# local policy\nprecedence ::/0 40\n")
    assert after_first_run.count(base_setup.IPV4_GAI_PRECEDENCE) == 1


def test_gai_conf_does_not_duplicate_an_active_equivalent_rule(tmp_path: Path) -> None:
    config = tmp_path / "gai.conf"
    original = (
        "# IPv4 is already preferred\n"
        "  precedence   ::ffff:0:0/96   100  # managed locally\n"
    )
    config.write_text(original, encoding="utf-8")

    base_setup.ensure_ipv4_egress_preference(config)

    assert config.read_text(encoding="utf-8") == original


def test_base_setup_applies_preference_before_network_bootstrap() -> None:
    source = _BASE_SETUP.read_text(encoding="utf-8")
    main = source.split("def main() -> None:\n", 1)[1]

    assert main.index("ensure_ipv4_egress_preference()") < main.index(
        'print("==> apt base packages")'
    )
