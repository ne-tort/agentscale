"""Static CI guards for infra/ops test coverage after merge to main.

G1 gap: ci-gate runs infra/ops tests only on pull_request, and verify-dev
historically had no pytest step at all, so ops regressions (e.g. runner
IPv4-preferred gai.conf, HTTPS apt sources) could disappear after merge.
verify-dev on main already installs infra/ops via Poetry; one pytest step
after Install and before Prepare Docker kubeconfig/Rollout closes that gap
without any new action, secret, install duplication or prod step.
"""

from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
VERIFY_DEV = (ROOT / ".github/workflows/verify-dev.yml").read_text(encoding="utf-8")
CI_GATE = (ROOT / ".github/workflows/ci-gate.yml").read_text(encoding="utf-8")


def test_verify_dev_has_pytest_step_in_infra_ops_before_rollout() -> None:
    step_start = VERIFY_DEV.index("- name: Run prodavan-ops unit tests (after merge gate)")
    next_step = VERIFY_DEV.index("- name: Prepare Docker kubeconfig")
    block = VERIFY_DEV[step_start:next_step]
    assert "working-directory: infra/ops" in block
    assert "poetry run pytest -q" in block
    assert VERIFY_DEV.index("poetry install --no-interaction") < step_start
    rollout = VERIFY_DEV.index("- name: Rollout first-party (after CI Images)")
    assert next_step < rollout


def test_verify_dev_install_is_not_duplicated() -> None:
    assert VERIFY_DEV.count("poetry install --no-interaction") == 1
    assert VERIFY_DEV.count("poetry run pytest -q") == 1


def test_verify_dev_uses_only_checkout_action() -> None:
    uses_lines = [
        line.strip()
        for line in VERIFY_DEV.splitlines()
        if line.strip().startswith("- uses:")
    ]
    assert uses_lines, "expected at least the checkout action"
    assert uses_lines == ["- uses: actions/checkout@v4"]


def test_verify_dev_has_no_prod_overlay_or_prod_target() -> None:
    for forbidden in (
        "overlays/prod",
        "agentscale-prod",
        "--app agentscale-prod",
        "--namespace agentscale-sandboxes",
        "--namespace agentscale\n",
    ):
        assert forbidden not in VERIFY_DEV
    assert "agentscale-dev" in VERIFY_DEV


def test_ci_gate_keeps_install_pytest_validate_order() -> None:
    install = CI_GATE.index("poetry install --no-interaction")
    tests = CI_GATE.index("poetry run pytest -q")
    validate = CI_GATE.index("poetry run prodavan-ops validate")
    assert install < tests < validate


def test_ci_gate_unchanged_prod_free_scope() -> None:
    assert "overlays/prod" not in CI_GATE
    assert "agentscale-prod" not in CI_GATE
