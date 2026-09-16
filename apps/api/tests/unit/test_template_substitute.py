"""Unit tests — template_substitute single-dialect (audit META-P1c)."""

from __future__ import annotations

from prodavan.application.projects.template_substitute import (
    deprecated_placeholder_names,
    find_placeholders,
    has_deprecated_placeholders,
    substitute,
)


def test_substitute_resolves_double_brace() -> None:
    assert substitute("packages/{{name}}", {"name": "echo"}) == "packages/echo"
    assert substitute("{{target_path}}", {"target_path": "assets/hello.txt"}) == (
        "assets/hello.txt"
    )


def test_substitute_leaves_unknown_keys_verbatim() -> None:
    # Unknown keys are not blanked — missing context keeps the placeholder.
    assert substitute("packages/{{name}}", {}) == "packages/{{name}}"


def test_substitute_none_value_left_verbatim() -> None:
    assert substitute("{{name}}", {"name": None}) == "{{name}}"


def test_substitute_does_not_match_single_brace() -> None:
    # META-P1c: single-brace {var} is no longer a placeholder. The inner
    # {target_path} of {{target_path}} must not be matched after the outer
    # placeholder resolves.
    out = substitute("{{target_path}}", {"target_path": "foo/{name}"})
    assert out == "foo/{name}"


def test_find_placeholders() -> None:
    assert find_placeholders("packages/{{name}}/{{target_path}}") == ["name", "target_path"]
    assert find_placeholders("no placeholders") == []


def test_has_deprecated_placeholders_detects_single_brace() -> None:
    assert has_deprecated_placeholders("{active_profile_id}")
    assert has_deprecated_placeholders("prefix/{name}/suffix")


def test_has_deprecated_placeholders_ignores_double_brace() -> None:
    assert not has_deprecated_placeholders("{{name}}")
    assert not has_deprecated_placeholders("packages/{{name}}/{{target_path}}")


def test_has_deprecated_placeholders_handles_mixed() -> None:
    # A mixed template (both {{var}} and {var}) is flagged because the
    # single-brace dialect is what we reject.
    assert has_deprecated_placeholders("{{target_path}}/{name}")


def test_deprecated_placeholder_names() -> None:
    assert deprecated_placeholder_names("{{target_path}}/{name}/{other}") == ["name", "other"]
    assert deprecated_placeholder_names("{{target_path}}") == []
