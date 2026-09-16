"""Single template substitution dialect for materialize meta (audit META-P1c).

Materialize rules use ``{{var}}`` Mustache placeholders for workspace_path /
template / filter values. A second, single-brace ``{var}`` regex dialect used
to live in ``MaterializePlanner._substitute`` and the two had to be applied in
a specific order to avoid ``{target_path}`` matching the inner braces of
``{{target_path}}``. That ordering was fragile and undocumented.

This module is the single source of truth for the dialect:

* ``{{var}}`` is the only supported placeholder;
* unknown keys are left as-is (so missing context does not blank paths);
* ``substitute`` is a pure function usable by the planner, the validator, and
  tests without instantiating the planner.

The old single-brace ``{var}`` dialect is rejected by the validator
(``module_meta_validator.validate_materialize_rule``) so meta authors cannot
silently rely on it.
"""

from __future__ import annotations

import re

#: The only supported placeholder syntax: ``{{identifier}}``.
_PLACEHOLDER_RE = re.compile(r"\{\{(\w+)\}\}")

#: Deprecated single-brace ``{identifier}`` syntax (rejected by validator).
_DEPRECATED_PLACEHOLDER_RE = re.compile(r"(?<!\{)\{([a-z_]\w*)\}(?!\})")


def substitute(template: str, ctx: dict[str, str | None]) -> str:
    """Render ``{{var}}`` placeholders from ``ctx``.

    Unknown keys are left as-is so a missing context value does not blank a
    path. ``None`` values are also left verbatim (the caller decides whether
    to omit the placeholder).
    """

    def repl(match: re.Match[str]) -> str:
        key = match.group(1)
        val = ctx.get(key)
        if val is None:
            return match.group(0)
        return val

    return _PLACEHOLDER_RE.sub(repl, template)


def substitute_blanking_missing(template: str, ctx: dict[str, str | None]) -> str:
    """Render ``{{var}}`` and blank **missing** keys to "".

    For file-content templates a literal ``{{var}}`` left in the rendered file
    would be confusing, so unresolved placeholders are dropped. Known ``None``
    values are also blanked. Shares the single ``{{var}}`` dialect with
    ``substitute`` (audit META-P1c).
    """

    def repl(match: re.Match[str]) -> str:
        key = match.group(1)
        val = ctx.get(key)
        if val is None:
            return ""
        return val

    return _PLACEHOLDER_RE.sub(repl, template)


def find_placeholders(template: str) -> list[str]:
    """Return the ``{{var}}`` identifiers referenced by ``template``."""
    return [m.group(1) for m in _PLACEHOLDER_RE.finditer(template)]


def has_deprecated_placeholders(template: str) -> bool:
    """True if ``template`` uses the deprecated single-brace ``{var}`` dialect.

    Double-brace ``{{var}}`` is not flagged (it is the supported syntax).
    Used by the meta validator to reject mixed/legacy dialects early.
    """
    # Avoid matching braces that are part of a {{var}} placeholder.
    stripped = _PLACEHOLDER_RE.sub("", template)
    return bool(_DEPRECATED_PLACEHOLDER_RE.search(stripped))


def deprecated_placeholder_names(template: str) -> list[str]:
    """Return single-brace ``{var}`` identifiers (for validator diagnostics)."""
    stripped = _PLACEHOLDER_RE.sub("", template)
    return [m.group(1) for m in _DEPRECATED_PLACEHOLDER_RE.finditer(stripped)]
