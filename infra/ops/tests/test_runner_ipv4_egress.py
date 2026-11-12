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


def _apt_tree(root: Path) -> Path:
    """Representative Ubuntu 24.04 apt tree: classic + deb822 + https-only repo."""
    apt = root / "apt"
    (apt / "sources.list.d").mkdir(parents=True)
    files = {
        "sources.list": (
            "deb http://archive.ubuntu.com/ubuntu noble main\n"
            "deb-src http://archive.ubuntu.com/ubuntu noble main\n"
            "deb http://deb.debian.org/debian bookworm main\n"
        ),
        "sources.list.d/ubuntu.sources": (
            "Types: deb\n"
            "URIs: http://archive.ubuntu.com/ubuntu/\n"
            "Suites: noble\n"
            "Components: main\n"
            "Signed-By: /usr/share/keyrings/ubuntu-archive-keyring.gpg\n"
            "\n"
            "Types: deb\n"
            "URIs: http://security.ubuntu.com/ubuntu/\n"
            "Suites: noble-security\n"
            "Components: main\n"
            "Signed-By: /usr/share/keyrings/ubuntu-archive-keyring.gpg\n"
        ),
        "sources.list.d/docker.list": (
            "deb https://download.docker.com/linux/ubuntu noble stable\n"
        ),
    }
    for name, text in files.items():
        path = apt / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return apt


def _snapshot(apt: Path) -> dict[str, str]:
    paths = [apt / "sources.list", *sorted((apt / "sources.list.d").glob("*"))]
    return {path.name: path.read_text(encoding="utf-8") for path in paths}


def test_apt_sources_rewrite_ubuntu_http_to_https(tmp_path: Path) -> None:
    apt = _apt_tree(tmp_path)

    base_setup.ensure_apt_sources_https(apt)

    sources_list = (apt / "sources.list").read_text(encoding="utf-8")
    deb822 = (apt / "sources.list.d/ubuntu.sources").read_text(encoding="utf-8")
    assert "http://archive.ubuntu.com" not in sources_list
    assert "https://archive.ubuntu.com/ubuntu" in sources_list
    assert "https://archive.ubuntu.com/ubuntu/" in deb822
    assert "https://security.ubuntu.com/ubuntu/" in deb822
    # Signature settings survive verbatim; only the scheme is rewritten.
    assert "Signed-By: /usr/share/keyrings/ubuntu-archive-keyring.gpg" in deb822
    # Non-Ubuntu hosts stay untouched: this helper must not overreach.
    assert "http://deb.debian.org/debian" in sources_list
    docker = (apt / "sources.list.d/docker.list").read_text(encoding="utf-8")
    assert "https://download.docker.com" in docker


def test_apt_sources_https_rewrite_is_idempotent(tmp_path: Path) -> None:
    apt = _apt_tree(tmp_path)

    base_setup.ensure_apt_sources_https(apt)
    after_first = _snapshot(apt)
    base_setup.ensure_apt_sources_https(apt)

    assert _snapshot(apt) == after_first
    assert not any(
        "http://archive.ubuntu.com" in text for text in after_first.values()
    )


def test_apt_sources_https_tolerates_missing_apt_tree(tmp_path: Path) -> None:
    base_setup.ensure_apt_sources_https(tmp_path / "missing")


def test_base_setup_rewrites_apt_sources_before_first_apt_update() -> None:
    source = _BASE_SETUP.read_text(encoding="utf-8")
    main = source.split("def main() -> None:\n", 1)[1]

    assert main.index("ensure_apt_sources_https()") < main.index(
        'print("==> apt base packages")'
    )
