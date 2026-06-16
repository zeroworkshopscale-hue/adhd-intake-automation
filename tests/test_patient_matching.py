"""Tests for the patient match-verification gate (prevents wrong-chart uploads)."""

from __future__ import annotations

import pytest

from adhd_intake.models import Demographics, parse_dob_candidates
from adhd_intake.oscar.client import OscarClient

m = OscarClient._is_confident_match


def _ext(last=None, first=None, dob=None):
    # dob given as the raw form string; matcher uses all readings of it.
    return Demographics(last_name=last, first_name=first, dob_raw=dob, dob=dob)


@pytest.mark.parametrize(
    "raw,expected_contains",
    [
        ("2001-05-10", "2001-05-10"),
        ("10/05/2001", "2001-05-10"),   # DD/MM/YYYY reading present
        ("05/10/2001", "2001-05-10"),   # MM/DD/YYYY reading present
        ("2001/05/10", "2001-05-10"),
        ("10-May-2001", "2001-05-10"),
        ("May 10, 2001", "2001-05-10"),
        ("10 May 2001", "2001-05-10"),
    ],
)
def test_dob_candidates_cover_common_formats(raw, expected_contains):
    assert expected_contains in parse_dob_candidates(raw)


def test_dob_candidates_ambiguous_returns_both():
    cands = parse_dob_candidates("05/10/2001")
    assert "2001-05-10" in cands and "2001-10-05" in cands


def test_match_resolves_format_against_chart():
    # Form says "10/05/2001" (ambiguous); OSCAR canonical is 2001-05-10.
    ok, _ = m(_ext("Cillo", "Julia", "10/05/2001"),
              {"last": "Cillo", "first": "Julia", "dob": "2001-05-10"})
    assert ok


def test_dob_plus_lastname_matches():
    ok, _ = m(_ext("Cillo", "Julia-rose", "2001-05-10"),
              {"last": "Cillo", "first": "Julia", "dob": "2001-05-10"})
    assert ok


def test_dob_mismatch_rejected():
    ok, reason = m(_ext("Cillo", "Julia", "2001-05-10"),
                   {"last": "Cillo", "first": "Julia", "dob": "1990-01-01"})
    assert not ok
    assert "DOB" in reason


def test_same_lastname_different_person_rejected_by_dob():
    # Two different "Smith" patients — DOB disambiguates and must reject.
    ok, _ = m(_ext("Smith", "John", "1985-03-03"),
              {"last": "Smith", "first": "Jane", "dob": "1972-11-20"})
    assert not ok


def test_dob_matches_but_lastname_differs_rejected():
    ok, reason = m(_ext("Cillo", "Julia", "2001-05-10"),
                   {"last": "Brown", "first": "Julia", "dob": "2001-05-10"})
    assert not ok
    assert "last name" in reason.lower()


def test_no_dob_uses_name_prefix():
    ok, _ = m(_ext("Cillo", "Julia"),
              {"last": "Cillo", "first": "Julianne", "dob": ""})
    assert ok  # first-3 (jul) + last-3 (cil) match


def test_no_dob_name_mismatch_rejected():
    ok, _ = m(_ext("Cillo", "Julia"),
              {"last": "Singh", "first": "Robbin", "dob": ""})
    assert not ok


def test_unreadable_chart_rejected():
    ok, _ = m(_ext("Cillo", "Julia", "2001-05-10"), {})
    assert not ok
