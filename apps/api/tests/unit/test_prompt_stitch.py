"""Unit tests for prompt fragment stitch."""

from prodavan.application.projects.prompt_stitch import (
    PromptContribution,
    stitch_prompt_contributions,
)


def test_stitch_orders_by_priority_ascending() -> None:
    stitched = stitch_prompt_contributions(
        [
            PromptContribution("AGENTS.md", 200, "second", fragment_id="b"),
            PromptContribution("AGENTS.md", 100, "first", fragment_id="a"),
        ]
    )
    assert stitched == {"AGENTS.md": "first\n\nsecond"}


def test_stitch_tie_break_shorter_body_first() -> None:
    stitched = stitch_prompt_contributions(
        [
            PromptContribution("rules/x.md", 100, "longer body", fragment_id="b"),
            PromptContribution("rules/x.md", 100, "short", fragment_id="a"),
        ]
    )
    assert stitched == {"rules/x.md": "short\n\nlonger body"}


def test_stitch_tie_break_fragment_id_when_same_length() -> None:
    stitched = stitch_prompt_contributions(
        [
            PromptContribution("a.md", 100, "bbbb", fragment_id="z", module_id="m2"),
            PromptContribution("a.md", 100, "aaaa", fragment_id="a", module_id="m1"),
        ]
    )
    assert stitched == {"a.md": "aaaa\n\nbbbb"}


def test_stitch_groups_by_workspace_path() -> None:
    stitched = stitch_prompt_contributions(
        [
            PromptContribution("a.md", 100, "A"),
            PromptContribution("b.md", 100, "B"),
            PromptContribution("a.md", 101, "A2"),
        ]
    )
    assert stitched == {"a.md": "A\n\nA2", "b.md": "B"}


def test_stitch_skips_empty_workspace_path() -> None:
    assert stitch_prompt_contributions([PromptContribution("", 100, "x")]) == {}
