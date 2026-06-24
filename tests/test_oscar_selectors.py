"""Guard against malformed OSCAR selectors.

A comma-separated CSS selector list is parsed by Playwright's CSS engine, so it
must not contain a bare engine prefix such as ``text=`` / ``xpath=`` — those
make the CSS parser throw "Unexpected token '='". Text matches inside a CSS list
must use the ``:has-text()`` / ``:text()`` CSS pseudo instead. (Regression test
for the login-verification crash.)
"""

from __future__ import annotations

import dataclasses

from adhd_intake.oscar.client import OscarSelectors

# Engine prefixes that are only valid as the FIRST token of a standalone
# selector, never as a member of a CSS comma list.
_BARE_ENGINE_PREFIXES = ("text=", "xpath=", "css=", "id=", "data-testid=")


def _is_comma_list(value: str) -> bool:
    return "," in value


def test_no_bare_engine_prefix_inside_css_comma_lists():
    sel = OscarSelectors()
    offenders = []
    for f in dataclasses.fields(sel):
        value = getattr(sel, f.name)
        if not isinstance(value, str) or not _is_comma_list(value):
            continue
        for part in value.split(","):
            part = part.strip()
            if part.startswith(_BARE_ENGINE_PREFIXES):
                offenders.append((f.name, part))
    assert not offenders, (
        "Selectors mix a bare engine prefix into a CSS comma list (use "
        f":has-text() instead): {offenders}"
    )


def test_login_success_marker_uses_has_text():
    sel = OscarSelectors()
    assert "text=" not in sel.login_success_marker.replace(":has-text", "").replace(
        ":text", ""
    )
    assert ":has-text(" in sel.login_success_marker
