from __future__ import annotations

import pytest

from prodavan_ops import validate


def test_no_flutter_frontend_passes_on_the_current_tree() -> None:
    """`apps/flutter` and any workflow reference are both gone."""
    validate.assert_no_flutter_frontend()


def test_no_flutter_frontend_rejects_the_directory(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """A restored `apps/flutter` must fail validation.

    The guard exists because the failure is silent: a workflow that still builds
    the Flutter Dockerfile produces a green pipeline and the wrong UI, which is
    how `agentscale.dev` served Flutter long after the TypeScript app landed.
    """
    (tmp_path / "apps" / "flutter").mkdir(parents=True)
    (tmp_path / ".github" / "workflows").mkdir(parents=True)
    monkeypatch.setattr(validate, "repo_root", lambda: tmp_path)

    with pytest.raises(RuntimeError, match="forbidden path"):
        validate.assert_no_flutter_frontend()


def test_no_flutter_frontend_rejects_a_workflow_reference(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    (tmp_path / "apps").mkdir()
    workflows = tmp_path / ".github" / "workflows"
    workflows.mkdir(parents=True)
    (workflows / "ci-images.yml").write_text(
        "jobs:\n  build-web:\n    steps:\n"
        "      - uses: docker/build-push-action@v6\n"
        "        with:\n          file: apps/flutter/Dockerfile\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(validate, "repo_root", lambda: tmp_path)

    with pytest.raises(RuntimeError, match="flutter references in workflows"):
        validate.assert_no_flutter_frontend()


def test_no_flutter_frontend_ignores_comments(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """A comment explaining the removal is not a reference to the old app."""
    (tmp_path / "apps").mkdir()
    workflows = tmp_path / ".github" / "workflows"
    workflows.mkdir(parents=True)
    (workflows / "ci-web.yml").write_text(
        "name: CI Web\n"
        "# The Flutter web app was removed; see apps/web/Dockerfile.\n"
        "jobs:\n  web:\n    runs-on: ubuntu-latest\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(validate, "repo_root", lambda: tmp_path)

    validate.assert_no_flutter_frontend()
