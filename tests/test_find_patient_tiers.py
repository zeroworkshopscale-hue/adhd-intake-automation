"""Tests for the escalating patient-match strategy (no browser needed).

A stub subclass overrides the two browser-touching helpers so we can drive
find_patient() purely on logic.
"""

from __future__ import annotations

import pytest

from adhd_intake.config import OscarConfig
from adhd_intake.models import Demographics
from adhd_intake.oscar.client import OscarClient, PatientNotFoundError


class StubClient(OscarClient):
    def __init__(self, search_map, details_map):
        super().__init__(OscarConfig(base_url="http://x", username="", password=""))
        self._search_map = search_map      # (mode, keyword) -> [demo, ...]
        self._details_map = details_map    # demo -> {last, first, dob, ...}

    def _search_candidates(self, mode, keyword):
        return list(self._search_map.get((mode, keyword), []))

    def _get_demographic_details(self, demo):
        d = dict(self._details_map.get(demo, {}))
        d.setdefault("display", ", ".join(p for p in (d.get("last"), d.get("first")) if p))
        return d


def _ext(last=None, first=None, dob=None, email=None):
    return Demographics(last_name=last, first_name=first, dob_raw=dob, dob=dob, email=email)


def test_tier1_exact_name_unique():
    c = StubClient(
        {("search_name", "Doe,Jane"): ["100"]},
        {"100": {"last": "Doe", "first": "Jane", "dob": "1990-05-01"}},
    )
    m = c.find_patient(_ext("Doe", "Jane", "1990-05-01"))
    assert m.demographic_no == "100"
    assert "exact name" in m.matched_by


def test_tier2_partial_3plus3_resolves_typo():
    # Exact name misses (last-name typo); first-3 + first-3 finds the chart.
    search = {
        ("search_name", "Cilllo,Julia"): [],
        ("search_name", "Cil,Jul"): ["50"],
    }
    details = {"50": {"last": "Cillo", "first": "Julia", "dob": "2001-05-10"}}
    c = StubClient(search, details)
    m = c.find_patient(_ext("Cilllo", "Julia", "2001-05-10"))
    assert m.demographic_no == "50"
    assert "partial" in m.matched_by


def test_ambiguous_escalates_to_dob_selection():
    # Both "Smith" share the DOB -> tier 1/2 are ambiguous -> operator selects.
    search = {
        ("search_name", "Smith,John"): ["1", "2"],
        ("search_name", "Smi,Joh"): ["1", "2"],   # tier 2: first-3 + first-3
        ("search_dob", "1985-03-03"): ["1", "2"],
    }
    details = {
        "1": {"last": "Smith", "first": "John", "dob": "1985-03-03"},
        "2": {"last": "Smith", "first": "Jane", "dob": "1985-03-03"},
    }
    c = StubClient(search, details)
    picked = {}

    def select_cb(cands):
        picked["count"] = len(cands)
        return "2"  # operator picks the second

    m = c.find_patient(_ext("Smith", "John", "1985-03-03"), select_cb=select_cb)
    assert picked["count"] == 2
    assert m.demographic_no == "2"
    assert "selected" in m.matched_by


def test_falls_through_to_email_prompt():
    search = {
        ("search_name", "Test,Herein"): [],
        ("search_name", "Tes,Her"): [],
        ("search_email", "h@example.com"): ["9"],
    }
    details = {"9": {"last": "Test", "first": "Herein", "dob": "1999-11-11"}}
    c = StubClient(search, details)
    asked = {}

    def email_cb(label):
        asked["label"] = label
        return "h@example.com"

    # No DOB so the DOB tier is skipped; email prompt resolves it.
    m = c.find_patient(_ext("Test", "Herein"), email_cb=email_cb)
    assert m.demographic_no == "9"
    assert m.matched_by == "email"
    assert "Test" in asked["label"]


def test_name_match_but_wrong_dob_is_offered_to_operator():
    # Real case: form DOB is mistyped (11-12) but chart DOB is 11-11. The unique
    # exact-name chart must be OFFERED to the operator (not silently dropped),
    # with a note explaining the DOB conflict.
    search = {
        ("search_name", "Test,Dhirender"): ["88018"],
        ("search_name", "Tes,Dhi"): ["88018"],
        ("search_dob", "1999-11-12"): [],   # the wrong DOB finds nobody
    }
    details = {"88018": {"last": "Test", "first": "Dhirender", "dob": "1999-11-11"}}
    c = StubClient(search, details)
    seen = {}

    def select_cb(cands):
        seen["cands"] = cands
        return "88018"

    m = c.find_patient(_ext("Test", "Dhirender", "1999-11-12"), select_cb=select_cb)
    assert m.demographic_no == "88018"
    assert len(seen["cands"]) == 1
    assert "note" in seen["cands"][0]
    assert "1999-11-11" in seen["cands"][0]["note"]  # shows the chart DOB
    assert "selected" in m.matched_by


def test_name_match_wrong_dob_headless_does_not_auto_upload():
    # Without an operator (no select_cb / email), a name match with a conflicting
    # DOB must NOT auto-match — safety against same-name, different-person charts.
    search = {
        ("search_name", "Test,Dhirender"): ["88018"],
        ("search_name", "Tes,Dhi"): ["88018"],
        ("search_dob", "1999-11-12"): [],
    }
    details = {"88018": {"last": "Test", "first": "Dhirender", "dob": "1999-11-11"}}
    c = StubClient(search, details)
    with pytest.raises(PatientNotFoundError):
        c.find_patient(_ext("Test", "Dhirender", "1999-11-12"))


def test_not_found_when_nothing_resolves():
    c = StubClient({}, {})
    with pytest.raises(PatientNotFoundError):
        c.find_patient(_ext("Nobody", "Here"))
